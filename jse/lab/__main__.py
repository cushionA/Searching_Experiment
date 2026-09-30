import argparse
import json
import sys
import zipfile
from pathlib import Path

from .engine import Engine, verify
from .state import LabError, Store, atomic, canonical, digest, encoded, locked


def read_json(path, cap=65536):
    with Path(path).open("rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise LabError("入力JSONがサイズ上限を超えています")
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise LabError("UTF-8のJSONが必要です") from None


def demo_config():
    return {"objective": "架空大学の教員プロフィールを発見する。fixtureによる動作検証であり実サイトの性能評価ではない。", "seeds": ["https://lab.example/"], "allowed_origins": ["https://lab.example"], "transport": "fixture", "user_agent": "DiscoveryLab/0.1 (offline-demo)", "delay_seconds": 0.5, "timeout_seconds": 10, "limits": {"pages_per_arm": 4, "attempts_per_arm": 8, "requests_per_arm": 12, "bytes_per_arm": 100000, "bytes_per_response": 20000, "max_depth": 3, "agent_requests": 8, "frontier_size": 100, "candidates_per_request": 50}}


def fixture_answer(request):
    base = {"request_id": request["request_id"]}
    if request["kind"] == "plan":
        return dict(base, hypothesis="模擬のリンク選択で研究者ページに早く到達する。", method="同じseed・4ページ上限でBFSと決定的な模擬選択器を比較する。", success_criterion="保存本文・引用・再開・上限を検証する。品質は評価しない。", risks="人工fixtureでありLunaの実測ではない。")
    if request["kind"] == "review":
        return dict(base, findings="fixtureの取得と保存物検証が完了した。", limitations="模擬回答でありLunaは呼んでいない。網羅性は未測定。", next_experiment="人が実サイトの対象と予算を決めて別runを作る。")
    candidates = request["context"]["candidates"]
    ordered = sorted(candidates, key=lambda c: (not any(word in c["anchor"] for word in ("研究", "教員")), c["url"]))
    observations = request["context"]["observations"]
    claims = [{"url": o["url"], "label": o["title"], "quote": o["text"][:100]} for o in observations if "教授" in o["text"]]
    return dict(base, next_urls=[ordered[0]["url"]] if ordered else [], stop=not bool(ordered), reason="fixture専用の決定的選択。Lunaではない。", claims=claims)


def run_demo(directory):
    with locked(directory):
        store = Store.create(directory, demo_config())
        engine = Engine(store)
        for _ in range(100):
            engine.advance()
            status = store.state["status"]
            if status == "awaiting_agent":
                request = read_json(store.directory / store.state["active_request"], 500000)
                engine.answer(fixture_answer(request))
            elif status == "awaiting_human":
                engine.decide(store.state["gate"]["id"], "approve", "オフラインdemo専用の模擬承認。実サイトには使わない。")
            elif status == "complete":
                return {"demo_only": True, "model_called": False, "status": store.status(), "verification": verify(store)}
            else:
                raise LabError(f"demoの予期しない停止: {status}")
        raise LabError("demoの反復上限です")


def export_checkpoint(store, output):
    if not verify(store)["ok"]:
        raise LabError("保存物の内部整合性に失敗したためexportできません")
    files = {"state.json"}
    for request in store.state["agent_requests"]:
        files.add(request["path"])
        if request.get("answer_path"):
            files.add(request["answer_path"])
    for call in store.state.get("model_calls", []):
        if call.get("path"):
            files.add(call["path"])
    for arm in store.state["arms"].values():
        for record in arm["http"]:
            if record.get("body_sha256"):
                files.add("blobs/" + record["body_sha256"])
        for page in arm["pages"]:
            files.add("blobs/" + page["text_sha256"])
        for rendering in arm.get("renderings", []):
            files.add("blobs/" + rendering["dom_sha256"])
    for name in ("decision.json", "DECISION.md", "report.json"):
        if (store.directory / name).exists():
            files.add(name)
    if Path(output).exists():
        raise LabError("既存のcheckpointを上書きできません")
    paths = {name: store.artifact(name) for name in files}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.write(paths[name], name)
    return {"checkpoint": str(output), "files": len(files), "state_hash": digest(store.state)}


def grade(store, gold, output):
    if store.state["phase"] not in ("review", "complete") or store.state["status"] not in ("awaiting_human", "complete", "rejected"):
        raise LabError("探索とモデルによる結果整理を終えてから、別の評価環境で採点してください")
    checked = verify(store)
    if not checked["ok"]:
        raise LabError("保存物検証に失敗したため採点できません")
    if not isinstance(gold, dict) or set(gold) != {"target_urls"} or not isinstance(gold["target_urls"], list) or not gold["target_urls"]:
        raise LabError("goldは空でないtarget_urls配列だけを持つJSONにしてください")
    targets = set(canonical(url) for url in gold["target_urls"])
    result = {"metric": "annotated_target_url_recall", "denominator": len(targets), "gold_hash": digest(gold), "note": "注釈したURL集合に限った到達率。日本Web全体の網羅率・意味的正解率ではない。探索時にgoldを渡さない。", "arms": {}}
    for name, arm in store.state["arms"].items():
        found = {p["url"] for p in arm["pages"]}
        hits = found & targets
        result["arms"][name] = {"hits": len(hits), "recall": len(hits) / len(targets), "missed_urls": sorted(targets - hits), "first_hit_attempt": min((p["attempt"] for p in arm["pages"] if p["url"] in targets), default=None)}
    atomic(output, encoded(result))
    return result


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="発見型クロールの実験・承認・検証。Codex Cloudのエージェントが回答する。")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "run", "status", "answer", "decide", "retry", "diagnose", "agent", "pause", "resume", "verify", "export", "grade", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--run", required=True, type=Path)
        if name == "init":
            command.add_argument("--config", required=True, type=Path)
        elif name == "answer":
            command.add_argument("--file", required=True, type=Path)
        elif name == "retry":
            command.add_argument("--note", required=True)
        elif name == "diagnose":
            command.add_argument("--arm", required=True, choices=("bfs", "luna"))
            command.add_argument("--client", required=True, choices=("impit", "urllib"))
        elif name == "agent":
            command.add_argument("--backend", required=True, choices=("responses",))
            command.add_argument("--model", default="gpt-6-luna")
            command.add_argument("--timeout", type=int, default=120)
            command.add_argument("--max-output-tokens", type=int, default=4000)
            command.add_argument("--max-calls", type=int, default=1)
            command.add_argument("--retry-call", action="store_true")
        elif name == "decide":
            command.add_argument("--id", required=True)
            command.add_argument("--decision", required=True, choices=("approve", "reject"))
            command.add_argument("--note", required=True)
        elif name in ("export", "grade"):
            command.add_argument("--output", required=True, type=Path)
            if name == "grade":
                command.add_argument("--gold", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            result = run_demo(args.run)
        elif args.command == "pause":
            if not (args.run / "state.json").is_file():
                raise LabError("実験がありません")
            atomic(args.run / "PAUSE", b"pause\n")
            result = {"pause_requested": True, "note": "実行中の取得後、次の取得前に停止します"}
        else:
            with locked(args.run):
                if args.command == "init":
                    store = Store.create(args.run, read_json(args.config))
                else:
                    store = Store(args.run)
                engine = Engine(store)
                result = None
                if args.command == "run":
                    engine.advance()
                elif args.command == "answer":
                    engine.answer(read_json(args.file))
                elif args.command == "decide":
                    engine.decide(args.id, args.decision, args.note)
                elif args.command == "retry":
                    engine.retry_failed(args.note)
                elif args.command == "diagnose":
                    result = engine.diagnose_proxy(args.arm, args.client)
                elif args.command == "agent":
                    from .agent import answer_with_model
                    result = answer_with_model(engine, args.backend, args.model, args.timeout,
                                               args.max_output_tokens, args.max_calls, args.retry_call)
                elif args.command == "resume":
                    engine.resume()
                elif args.command == "verify":
                    result = verify(store)
                elif args.command == "export":
                    result = export_checkpoint(store, args.output)
                elif args.command == "grade":
                    result = grade(store, read_json(args.gold), args.output)
                if result is None:
                    result = store.status()
        print(encoded(result).decode("utf-8"))
        return 1 if result.get("ok") is False else 0
    except (LabError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
