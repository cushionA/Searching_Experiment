"""Run a separate, explicitly selected model for exactly one active lab request."""
import json
import os
import re
import time
import urllib.request

from . import MODEL
from .network import error_diagnostics, environment_proxy
from .state import LabError, digest, encoded


API_URL = "https://api.openai.com/v1/responses"


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def answer_schema(kind, discovery=False):
    text = {"type": "string"}
    properties = {"request_id": text}
    if kind == "plan":
        properties.update({key: text for key in ("hypothesis", "method", "success_criterion", "risks")})
    elif kind == "review":
        if discovery:
            evidence = object_schema({"url": text, "quote": text})
            properties.update(answer={"type": "array", "maxItems": 10,
                                     "items": object_schema({"statement": text, "evidence": {"type": "array", "minItems": 1, "maxItems": 3, "items": evidence}})})
            properties.update({key: text for key in ("unanswered", "limitations", "next_experiment")})
        else:
            properties.update({key: text for key in ("findings", "limitations", "next_experiment")})
    elif kind == "explore":
        properties.update(next_urls={"type": "array", "items": text, "maxItems": 4},
                          stop={"type": "boolean"}, reason=text,
                          claims={"type": "array", "maxItems": 10, "items": object_schema({key: text for key in ("url", "label", "quote")})})
        if discovery:
            properties["search_queries"] = {"type": "array", "maxItems": 2, "items": text}
    else:
        raise LabError("未知のagent要求種別です")
    return object_schema(properties)


def model_evidence(state):
    answered = [r for r in state["agent_requests"] if r["answered"]]
    calls = state.get("model_calls", [])
    verified = bool(answered) and all(any(c.get("status") == "accepted" and c["request_id"] == r["id"]
                         and c.get("model_runtime_verified") and c.get("answer_hash") == r["answer_hash"] for c in calls) for r in answered)
    return {"model_runtime_verified": verified, "model_calls": len(calls),
            "model_usage": [c["usage"] for c in calls if c.get("usage")], "model_cost": None}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def responses_answer(request, model, timeout, max_output_tokens):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise LabError("OPENAI_API_KEYが実行プロセスにありません。Cloudの親認証はAPIへ継承されません")
    payload = {"model": model, "store": False, "max_output_tokens": max_output_tokens,
               "reasoning": {"effort": "high"}, "instructions": request["instructions"] + "\nresponse_formatの説明に従い、回答JSONだけを返してください。",
               "input": encoded(request).decode("utf-8"),
               "text": {"format": {"type": "json_schema", "name": "lab_answer", "strict": True, "schema": answer_schema(request["kind"], "search_queries" in request["response_format"] or "answer" in request["response_format"])}}}
    http_request = urllib.request.Request(API_URL, data=encoded(payload), method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(http_request, timeout=timeout) as response:
        raw = response.read(1000001)
    if len(raw) > 1000000:
        raise LabError("モデル応答がサイズ上限を超えています")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("status") != "completed":
        raise LabError("モデル応答が完了していません。自動再試行しません")
    served = data.get("model", "")
    if not isinstance(served, str) or not re.fullmatch(re.escape(model) + r"(?:-\d{4}-\d{2}-\d{2})?", served):
        raise LabError("APIの応答モデルが要求モデルと一致しません")
    texts = []
    for item in data.get("output", []):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise LabError("モデルが回答を拒否しました")
            if part.get("type") == "output_text":
                texts.append(part["text"])
    answer = json.loads("".join(texts))
    return answer, {"model_runtime_verified": True, "model_returned": served,
                    "verification_source": "responses_api_model_field", "response_id": data.get("id"), "usage": data.get("usage")}


def answer_with_model(engine, backend, model=MODEL, timeout=120, max_output_tokens=4000, max_calls=1, retry_call=False):
    if model != MODEL:
        raise LabError(f"この実験の調査担当は{MODEL}です。代替モデルへ自動変更できません")
    if backend != "responses" or not 1 <= timeout <= 300 or not 128 <= max_output_tokens <= 16000 or not 1 <= max_calls <= 3:
        raise LabError("backendまたはモデル実行の上限が不正です")
    state = engine.s
    if state["status"] != "awaiting_agent" or not state["active_request"]:
        raise LabError("awaiting_agentでのみ別モデルを実行できます")
    record = state["agent_requests"][-1]
    request = json.loads(engine.store.artifact(record["path"]).read_text(encoding="utf-8"))
    if digest(request) != record["hash"] or request.get("model_requested") != model:
        raise LabError("active_requestが変更されているかモデルが一致しません")
    # Check installation/credentials before reserving a paid inference attempt.
    if backend == "responses" and not os.environ.get("OPENAI_API_KEY"):
        raise LabError("OPENAI_API_KEYが実行プロセスにありません。API利用は別課金です")
    calls = state.setdefault("model_calls", [])
    previous = [c for c in calls if c["request_id"] == record["id"]]
    if len(previous) >= max_calls:
        raise LabError("この要求のモデル呼出し上限です。失敗や中断も数えます")
    if previous and not retry_call:
        raise LabError("既存のモデル呼出しがあります。確認後に--retry-callと--max-callsを明示してください")
    call = {"request_id": record["id"], "request_hash": record["hash"], "backend": backend,
            "model_requested": model, "model_runtime_verified": False, "status": "interrupted",
            "time": time.time(), "timeout_seconds": timeout, "max_output_tokens_requested": max_output_tokens}
    calls.append(call)
    path = f"model-calls/{len(calls):03d}.json"
    engine.store.write(path, call)
    call.update(path=path, hash=digest(call))
    engine.store.save()
    metadata = {}
    try:
        answer, metadata = responses_answer(request, model, timeout, max_output_tokens)
        call.update(metadata)
        call["status"] = "received"
        envelope = {key: value for key, value in call.items() if key not in ("path", "hash")}
        engine.store.write(path, envelope)
        call["hash"] = digest(envelope)
        engine.answer(answer)
        call.update(status="accepted", answer_hash=record["answer_hash"])
    except (LabError, OSError, ValueError) as error:
        call.update(status="failed", **error_diagnostics(error, environment_proxy(API_URL)))
        raise LabError("別モデルの実行に失敗: " + call["error_detail"]) from None
    finally:
        envelope = {key: value for key, value in call.items() if key not in ("path", "hash")}
        engine.store.write(path, envelope)
        call.update(path=path, hash=digest(envelope))
        engine.store.event("model_call", {"request_id": record["id"], "backend": backend, "status": call["status"]})
    if state["phase"] == "review" and state["status"] == "awaiting_human":
        from .engine import verify
        review = state["gate"]["content"]["review"]
        report = verify(engine.store)
        engine.store.write("report.json", report)
        engine.gate("result", {"review": review, "report": report})
    return {"status": engine.store.status(), "model_call": call}
