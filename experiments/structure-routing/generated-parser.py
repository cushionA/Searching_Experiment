import argparse
import ast
import hashlib
import json
import pathlib
import sys


ALLOWED_IMPORT_ROOTS = {"json", "re", "lxml", "pathlib"}
ALLOWED_BUILTINS = {
    "__build_class__": __build_class__,
    "object": object,
    "str": str,
    "dict": dict,
    "list": list,
    "tuple": tuple,
    "set": set,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "isinstance": isinstance,
    "any": any,
    "all": all,
    "sorted": sorted,
    "min": min,
    "max": max,
    "sum": sum,
    "ValueError": ValueError,
    "TypeError": TypeError,
}
ALLOWED_CALLS = {
    ("lxml_html", "fromstring"),
    ("tree", "xpath"),
    ("url", "strip"),
}


class LoaderError(ValueError):
    pass


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _is_main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
        and len(node.body) == 1
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Call)
        and isinstance(node.body[0].value.func, ast.Name)
        and node.body[0].value.func.id == "main"
        and not node.body[0].value.args
        and not node.body[0].value.keywords
        and not node.orelse
    )


def _imports(tree):
    imported = {}
    found = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    raise LoaderError(f"blocked import: {alias.name}")
                local_name = alias.asname or alias.name.split(".", 1)[0]
                imported[local_name] = (alias.name, alias.asname)
                found.append((local_name, alias.name, alias.asname, node))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise LoaderError("relative imports are blocked")
            root = node.module.split(".", 1)[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                raise LoaderError(f"blocked import: {node.module}")
            for alias in node.names:
                if alias.name == "*":
                    raise LoaderError("star imports are blocked")
                local_name = alias.asname or alias.name
                imported[local_name] = (node.module, alias.name)
                found.append((local_name, node.module, alias.name, node))
    return imported, found


def _validate_parser_class(class_node):
    if class_node.name != "WebPageParser" or class_node.bases or class_node.keywords or class_node.decorator_list:
        raise LoaderError("expected a plain WebPageParser class")
    if len(class_node.body) != 1 or not isinstance(class_node.body[0], ast.FunctionDef):
        raise LoaderError("class must contain only its parse method")
    function = class_node.body[0]
    if function.name != "parse" or function.decorator_list or function.returns is None:
        raise LoaderError("expected an annotated parse method")
    if [arg.arg for arg in function.args.args] != ["self", "html_text"]:
        raise LoaderError("parse must accept self and html_text")
    if not (
        function.args.args[0].annotation is None
        and isinstance(function.args.args[1].annotation, ast.Name)
        and function.args.args[1].annotation.id == "str"
        and isinstance(function.returns, ast.Name)
        and function.returns.id == "dict"
    ):
        raise LoaderError("parse annotations must be html_text: str -> dict")
    if function.args.posonlyargs or function.args.kwonlyargs or function.args.vararg or function.args.kwarg:
        raise LoaderError("parse signature has unsupported arguments")
    if function.args.defaults or function.args.kw_defaults:
        raise LoaderError("parse defaults are blocked")
    if len(function.body) != 3:
        raise LoaderError("parse method body has unsupported statements")
    assign_tree, assign_urls, returned = function.body
    if not (
        isinstance(assign_tree, ast.Assign)
        and len(assign_tree.targets) == 1
        and isinstance(assign_tree.targets[0], ast.Name)
        and assign_tree.targets[0].id == "tree"
        and isinstance(assign_urls, ast.Assign)
        and len(assign_urls.targets) == 1
        and isinstance(assign_urls.targets[0], ast.Name)
        and assign_urls.targets[0].id == "product_urls"
        and isinstance(returned, ast.Return)
        and isinstance(returned.value, ast.Dict)
    ):
        raise LoaderError("parse method structure is not supported")
    call = assign_tree.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "lxml_html"
        and call.func.attr == "fromstring"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "html_text"
        and not call.keywords
    ):
        raise LoaderError("only lxml_html.fromstring(html_text) is allowed")
    xpath_call = assign_urls.value
    if not (
        isinstance(xpath_call, ast.Call)
        and isinstance(xpath_call.func, ast.Attribute)
        and isinstance(xpath_call.func.value, ast.Name)
        and xpath_call.func.value.id == "tree"
        and xpath_call.func.attr == "xpath"
        and len(xpath_call.args) == 1
        and isinstance(xpath_call.args[0], ast.Constant)
        and isinstance(xpath_call.args[0].value, str)
        and xpath_call.args[0].value.startswith("//")
        and not xpath_call.keywords
    ):
        raise LoaderError("xpath must be a constant absolute selector")
    output = returned.value
    if len(output.keys) != 1 or not isinstance(output.keys[0], ast.Constant) or output.keys[0].value != "product_urls":
        raise LoaderError("parse must return only product_urls")
    values = output.values[0]
    if not isinstance(values, ast.ListComp) or len(values.generators) != 1:
        raise LoaderError("product_urls must be a single list comprehension")
    generator = values.generators[0]
    if not (
        isinstance(generator.target, ast.Name)
        and generator.target.id == "url"
        and isinstance(generator.iter, ast.Name)
        and generator.iter.id == "product_urls"
        and len(generator.ifs) == 1
        and not generator.is_async
    ):
        raise LoaderError("product_urls comprehension has unsupported structure")
    for node in ast.walk(class_node):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Lambda, ast.With, ast.Try, ast.Global, ast.Nonlocal)):
            raise LoaderError(f"blocked class syntax: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise LoaderError(f"dunder attribute blocked: {node.attr}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name):
                raise LoaderError("only approved parser method calls are allowed")
            pair = (node.func.value.id, node.func.attr)
            if pair not in ALLOWED_CALLS:
                raise LoaderError(f"blocked call: {pair[0]}.{pair[1]}")
            if pair == ("url", "strip") and (node.args or node.keywords):
                raise LoaderError("strip arguments are blocked")
    return xpath_call.args[0].value


def load_parser(code_path):
    raw = pathlib.Path(code_path).read_bytes()
    try:
        code = raw.decode("utf-8")
        tree = ast.parse(code, filename=str(code_path))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise LoaderError(f"invalid generated source: {exc}") from exc
    imported, import_nodes = _imports(tree)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if len(classes) != 1:
        raise LoaderError("source must define exactly one class")
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)):
            continue
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            continue
        if _is_main_guard(node):
            continue
        raise LoaderError(f"unsupported top-level statement: {type(node).__name__}")
    class_node = classes[0]
    selector = _validate_parser_class(class_node)
    class_source = ast.get_source_segment(code, class_node)
    if not class_source:
        raise LoaderError("could not extract class source")
    referenced = {node.id for node in ast.walk(class_node) if isinstance(node, ast.Name)}
    required_imports = []
    dropped_imports = []
    for local_name, module, alias, node in import_nodes:
        if local_name in referenced:
            if module != "lxml" or local_name != "lxml_html" or alias != "html":
                raise LoaderError(f"only lxml.html as lxml_html is required and supported; got {module}")
            required_imports.append("from lxml import html as lxml_html")
        else:
            dropped_imports.append(module if alias is None else f"{module} as {alias}")
    if required_imports != ["from lxml import html as lxml_html"]:
        raise LoaderError("required lxml.html import is missing")
    sanitized = "\n".join(required_imports) + "\n\n" + class_source + "\n"
    sanitized_tree = ast.parse(sanitized)
    original_class_ast_sha256 = sha256_bytes(ast.dump(class_node, include_attributes=False).encode())
    sanitized_class = next(node for node in sanitized_tree.body if isinstance(node, ast.ClassDef))
    if ast.dump(class_node, include_attributes=False) != ast.dump(sanitized_class, include_attributes=False):
        raise LoaderError("extracted parser class AST changed")
    safe_builtins = dict(ALLOWED_BUILTINS)

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name != "lxml":
            raise ImportError(f"blocked import: {name}")
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins["__import__"] = safe_import
    namespace = {"__builtins__": safe_builtins, "__name__": "extracted_parser"}
    exec(compile(sanitized_tree, "<extracted-WebPageParser>", "exec"), namespace)
    parser_class = namespace.get("WebPageParser")
    if parser_class is None:
        raise LoaderError("WebPageParser class was not loaded")
    audit = {
        "raw_source_sha256": sha256_bytes(raw),
        "sanitized_source_sha256": sha256_bytes(sanitized.encode()),
        "sanitized_ast_sha256": sha256_bytes(ast.dump(sanitized_tree, include_attributes=False).encode()),
        "original_class_ast_sha256": original_class_ast_sha256,
        "executed_class_ast_sha256": sha256_bytes(ast.dump(sanitized_class, include_attributes=False).encode()),
        "class_ast_unchanged": original_class_ast_sha256 == sha256_bytes(ast.dump(sanitized_class, include_attributes=False).encode()),
        "selector": selector,
        "retained_imports": required_imports,
        "removed_imports": dropped_imports,
        "removed_top_level_functions": [node.name for node in tree.body if isinstance(node, ast.FunctionDef)],
        "excluded_main_guard": any(_is_main_guard(node) for node in tree.body),
        "execution_boundary": "AST-validated class-only execution with limited builtins; not an OS sandbox",
    }
    return parser_class(), audit


def parse_html(parser, html_text):
    value = parser.parse(html_text)
    if not isinstance(value, dict) or set(value) != {"product_urls"}:
        raise LoaderError("parse must return exactly {'product_urls': list[str]}")
    urls = value["product_urls"]
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise LoaderError("product_urls must be list[str]")
    return {"product_urls": urls}


def main(argv=None):
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--code", required=True)
    argument_parser.add_argument("--html", required=True)
    argument_parser.add_argument("--output")
    args = argument_parser.parse_args(argv)
    parser, audit = load_parser(args.code)
    html_text = pathlib.Path(args.html).read_text(encoding="utf-8")
    result = parse_html(parser, html_text)
    result["_loader_audit"] = audit
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return audit


if __name__ == "__main__":
    main()
