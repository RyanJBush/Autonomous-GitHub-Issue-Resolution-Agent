"""LIMITED AST pre-screen of a resulting file. Not a security boundary (Codex M3 decision).

It parses text with `ast.parse` (no compile, no import, no execution) and flags constructs a reviewer must treat as
suspicious. Passing it means only that none of these constructs appear; a human must still read the whole resulting
file and its effects before any replay is approved.
"""
from __future__ import annotations

import ast

LABEL = "LIMITED AST PRE-SCREEN (parses only; not a security boundary; human review of the whole file is required)"
FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "input", "breakpoint", "memoryview", "os", "sys", "subprocess", "socket", "builtins", "importlib", "ctypes",
    "shutil", "pathlib", "signal", "threading", "multiprocessing", "pickle", "marshal", "atexit", "io", "time",
})
ALLOWED_MODULE_LEVEL = (ast.FunctionDef, ast.ClassDef)


def prescreen(new_content: bytes) -> dict:
    findings = []
    try:
        tree = ast.parse(new_content.decode("utf-8"), mode="exec")
    except (SyntaxError, ValueError, UnicodeDecodeError) as error:
        return {"label": LABEL, "passed": False, "findings": [f"syntax_error:{type(error).__name__}"]}
    for i, stmt in enumerate(tree.body):
        is_docstring = (i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str))
        if not (is_docstring or isinstance(stmt, ALLOWED_MODULE_LEVEL)):
            findings.append(f"module_level_statement:{type(stmt).__name__}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            findings.append("import_statement")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            findings.append(f"scope_statement:{type(node).__name__}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"forbidden_name:{node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            findings.append(f"dunder_attribute:{node.attr}")
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.decorator_list:
            findings.append("decorator")
    return {"label": LABEL, "passed": not findings, "findings": sorted(set(findings))[:50]}
