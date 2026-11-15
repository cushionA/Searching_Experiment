import argparse
import ast
import hashlib
import json
import sys
import time
from pathlib import Path

import psutil
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / ".deps/structure-routing-models/Qwen-HTML"
RUN = ROOT / "lab-runs/structure-routing-all-candidates-20261115/qwen-html"
INPUT = ROOT / "lab-runs/structure-routing-all-candidates-20261115/common-inputs/01"
MODEL_LOAD_SECONDS = 0.0


class InferenceFailure(RuntimeError):
    def __init__(self, message, metrics):
        super().__init__(message)
        self.metrics = metrics


def load_model():
    global MODEL_LOAD_SECONDS
    started = time.perf_counter()
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL, local_files_only=True, trust_remote_code=False
    )
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.float16,
        quantization_config=quantization,
        device_map="auto",
        max_memory={0: "7000MiB", "cpu": "22GiB"},
        low_cpu_mem_usage=True,
    )
    model.eval()
    MODEL_LOAD_SECONDS = round(time.perf_counter() - started, 3)
    return tokenizer, model


def generate(tokenizer, model, messages, max_new_tokens):
    encoded = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        enable_thinking=False,
    )
    input_ids = encoded["input_ids"]
    device = model.get_input_embeddings().weight.device
    input_ids = input_ids.to(device)
    started = time.perf_counter()
    try:
        with torch.inference_mode():
            output = model.generate(
                input_ids=input_ids,
                attention_mask=encoded["attention_mask"].to(device),
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
            )
    except Exception as exc:
        metrics = {
            "input_tokens": int(input_ids.shape[-1]),
            "output_tokens": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }
        torch.cuda.empty_cache()
        raise InferenceFailure(metrics["error"], metrics) from exc
    elapsed = time.perf_counter() - started
    generated = output[0, input_ids.shape[-1]:]
    return tokenizer.decode(generated, skip_special_tokens=True), {
        "input_tokens": int(input_ids.shape[-1]),
        "output_tokens": int(generated.shape[-1]),
        "elapsed_seconds": round(elapsed, 3),
    }


def parse_rule(output):
    value = None
    for start, char in enumerate(output):
        if char != "{":
            continue
        try:
            candidate, _ = json.JSONDecoder().raw_decode(output[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            value = candidate
            break
    if not value:
        return None
    selector = value.get("selector")
    selector_type = value.get("selector_type")
    if not isinstance(selector, str) or not selector.strip():
        return None
    if selector_type not in {"css", "xpath"}:
        return None
    return {
        "kind": "selector",
        "selector": selector.strip(),
        "selector_type": selector_type,
        "attribute": "href",
    }


def apply_rule(rule, html):
    if rule["selector_type"] == "css":
        from bs4 import BeautifulSoup

        nodes = BeautifulSoup(html, "html.parser").select(rule["selector"])
    else:
        from lxml import html as lxml_html

        nodes = lxml_html.fromstring(html).xpath(rule["selector"])
    return [node.get("href") for node in nodes if getattr(node, "get", None) and node.get("href")]


def run_selector(args, tokenizer, model):
    seed_path = INPUT / "seed-1.dom.html"
    html = seed_path.read_text(encoding="utf-8")
    query = "Select links to individual product detail pages from a product listing. Exclude category, navigation, account and pagination links. Save a rule that can be reused on another listing."
    system = (
        "You identify reusable HTML link selectors. Inspect only the supplied representative HTML. "
        "Return exactly one JSON object with fields kind='selector', selector, selector_type='css' or 'xpath', attribute='href'. "
        "Choose a selector for repeated individual product detail links. Do not include navigation, category, account, or pagination links. "
        "Use a stable CSS selector or XPath that is reusable across structurally similar listing pages."
    )
    prompt = f"Task: {query}\nRepresentative HTML:\n{html}"
    try:
        output, metrics = generate(
            tokenizer,
            model,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            args.max_new_tokens,
        )
    except InferenceFailure as exc:
        output, metrics = "", exc.metrics
    rule = parse_rule(output)
    result = {
        "model_id": "Ambition0303/Qwen-HTML",
        "revision": "b1b061d3f708807534d3796414e0c882ac46e883",
        "quantization": "bitsandbytes 4-bit NF4, double quantization",
        "compute_dtype": "float16",
        "trust_remote_code": False,
        "model_load_seconds": MODEL_LOAD_SECONDS,
        "input_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "input_chars": len(html),
        "metrics": metrics,
        "generated_text": output,
        "rule": rule,
        "tests": [],
    }
    result["error"] = metrics.get("error")
    if result["rule"]:
        rule = result["rule"]
        for index in (1, 2, 3):
            page = (INPUT / f"seed-{index}.dom.html").read_text(encoding="utf-8")
            try:
                links = apply_rule(rule, page)
                result["tests"].append({
                    "seed": index,
                    "input_chars": len(page),
                    "candidate_count": len(links),
                    "links_sha256": hashlib.sha256(
                        json.dumps(links, ensure_ascii=False).encode()
                    ).hexdigest(),
                    "links": links,
                })
            except Exception as exc:
                result["tests"].append({"seed": index, "error": f"{type(exc).__name__}: {exc}"})
    devices = {}
    for parameter in model.parameters():
        device = str(parameter.device)
        devices[device] = devices.get(device, 0) + parameter.numel()
    result["parameter_devices"] = devices
    result["gpu_peak_memory_bytes"] = torch.cuda.max_memory_allocated()
    result["process_peak_rss_bytes"] = psutil.Process().memory_info().peak_wset
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / "selector-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in ("metrics", "rule", "tests", "gpu_peak_memory_bytes")}, ensure_ascii=False))


def run_coscraper(args, tokenizer, model):
    source = ROOT / "lab-runs/structure-routing-20261115/candidate-audit/co-scraper/src"
    sys.path.insert(0, str(source))
    from coscraper import pipeline

    calls = []

    def local_inference(system_prompt, user_prompt, *, max_tokens, **kwargs):
        try:
            output, metrics = generate(
                tokenizer,
                model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                min(max_tokens, args.max_new_tokens),
            )
        except InferenceFailure as exc:
            calls.append(exc.metrics)
            raise
        calls.append(metrics)
        return "", output

    pipeline.inference = local_inference
    htmls = [(INPUT / f"seed-{index}.dom.html").read_text(encoding="utf-8") for index in (1, 2, 3)]
    schema = {
        "type": "object",
        "properties": {"product_urls": {"type": "array", "items": {"type": "string"}}},
        "required": ["product_urls"],
    }
    started = time.perf_counter()
    try:
        result = pipeline.run(
            "Extract the href URLs for individual product detail pages in a product listing.",
            schema,
            htmls,
            sources=[f"seed-{index}" for index in (1, 2, 3)],
            id_max_tokens=args.max_new_tokens,
            code_max_tokens=args.max_new_tokens,
            max_attempts=1,
            execute=False,
            output_dir=RUN / "coscraper",
        )
    except Exception as exc:
        outcome = {
            "model_id": "Ambition0303/Qwen-HTML",
            "revision": "b1b061d3f708807534d3796414e0c882ac46e883",
            "pipeline": "Co-Scraper source revision a83a1b9740b8883c25365316728c890d4ff68c0e",
            "input_mode": "Co-Scraper simplified and query-aware pruned DOM from three seeds",
            "input_chars": [len(page) for page in htmls],
            "model_load_seconds": MODEL_LOAD_SECONDS,
            "model_calls": calls,
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "executed": False,
        }
        RUN.mkdir(parents=True, exist_ok=True)
        (RUN / "coscraper-execution.json").write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(outcome, ensure_ascii=False))
        return
    code = result["generated_code"]
    safety = inspect_generated_code(code)
    outcome = {
        "model_id": "Ambition0303/Qwen-HTML",
        "revision": "b1b061d3f708807534d3796414e0c882ac46e883",
        "pipeline": "Co-Scraper source revision a83a1b9740b8883c25365316728c890d4ff68c0e",
        "input_mode": "Co-Scraper simplified and query-aware pruned DOM from three seeds",
        "input_chars": [len(page) for page in htmls],
        "model_calls": calls,
        "input_tokens": sum(call["input_tokens"] for call in calls),
        "output_tokens": sum(call["output_tokens"] for call in calls),
        "model_load_seconds": MODEL_LOAD_SECONDS,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "gpu_peak_memory_bytes": torch.cuda.max_memory_allocated(),
        "process_peak_rss_bytes": psutil.Process().memory_info().peak_wset,
        "generated_code_safety": safety,
        "executed": False,
        "execution_results": None,
    }
    if safety["safe"]:
        try:
            parser = load_generated_parser(code)
            runs = []
            for index, page in enumerate(htmls, start=1):
                value = parser.parse(page)
                runs.append({"seed": index, "result": value})
            outcome["executed"] = True
            outcome["execution_results"] = runs
        except Exception as exc:
            outcome["execution_error"] = f"{type(exc).__name__}: {exc}"
    (RUN / "coscraper-execution.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(outcome, ensure_ascii=False))


def inspect_generated_code(code):
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"safe": False, "reason": f"SyntaxError: {exc}"}
    allowed_modules = {"json", "re", "lxml"}
    imported = []
    denied = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            denied.append(f"dunder attribute: {node.attr}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "open", "eval", "exec", "compile", "input", "__import__", "getattr", "hasattr"
            }:
                denied.append(f"call: {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
                receiver = node.func.value
                if name in {
                    "open", "read_text", "write_text", "read_bytes", "write_bytes", "read",
                    "write", "unlink", "remove", "mkdir", "rmdir", "exists", "is_file",
                    "is_dir", "connect", "send", "request", "post", "put", "delete",
                    "urlopen", "system", "popen", "Popen", "run", "check_call", "check_output",
                }:
                    denied.append(f"attribute call: {name}")
                if name == "parse" and not (
                    isinstance(receiver, ast.Name) and receiver.id == "parser"
                ):
                    denied.append("non-parser parse call")
    blocked_modules = sorted({name.split(".")[0] for name in imported} - allowed_modules)
    return {
        "safe": not blocked_modules and not denied,
        "imports": imported,
        "blocked_modules": blocked_modules,
        "blocked_calls": sorted(set(denied)),
    }


def load_generated_parser(code):
    allowed_modules = {"json", "re", "lxml"}

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split(".")[0] not in allowed_modules:
            raise ImportError(f"blocked import: {name}")
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins = {
        "__build_class__": __build_class__,
        "__import__": safe_import,
        "object": object,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "len": len,
        "enumerate": enumerate,
        "zip": zip,
        "range": range,
        "isinstance": isinstance,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "Exception": Exception,
        "RuntimeError": RuntimeError,
        "any": any,
        "all": all,
        "sorted": sorted,
    }
    namespace = {"__builtins__": safe_builtins, "__name__": "coscraper_generated_code"}
    exec(compile(code, "<generated-coscraper>", "exec"), namespace)
    parser_type = namespace.get("WebPageParser")
    if parser_type is None:
        raise ValueError("generated code does not define WebPageParser")
    return parser_type()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("selector", "coscraper", "all"), default="all")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the configured 4-bit inference path")
    torch.cuda.reset_peak_memory_stats()
    tokenizer, model = load_model()
    if args.mode in {"selector", "all"}:
        run_selector(args, tokenizer, model)
    if args.mode in {"coscraper", "all"}:
        run_coscraper(args, tokenizer, model)


if __name__ == "__main__":
    main()
