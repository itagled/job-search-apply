#!/usr/bin/env python3
"""Repo verification battery: compiles, imports, and asks --help from each CLI.

Runs with fake keys and a temporary SQLite (already-set variables win over
the ``.env``), so an error never reaches a paid API or the real DB. If there
is ``tests/`` it runs pytest, and if there is ``pyproject.toml``, ruff.

Usage:
    python dev/verify.py --out base.json
    python dev/verify.py --out now.json --compare base.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "data",
    "secrets",
    "profile",
    ".claude",
    ".github",
    "node_modules",
    "build",
    "dist",
    "migrations",  # env.py only runs inside Alembic; the tests cover the migrations
}
NEVER_RUN = ("reauth",)  # open Google consent in the browser
SAFE_TOP_CALLS = {
    "sys.path.insert",
    "sys.path.append",
    "load_env_files",
    "setup_import_paths",
    "load_dotenv",
    "sys.stdout.reconfigure",
    "sys.stderr.reconfigure",
    "warnings.filterwarnings",
    "logging.basicConfig",
    "register",
    "config.register",
}


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _import_safe(tree: ast.Module) -> bool:
    """True if importing the module only defines things (does not run work)."""
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.If,
        ast.Try,
        ast.Assign,
        ast.AnnAssign,
    )
    for node in tree.body:
        if isinstance(node, allowed):
            continue
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant):
                continue
            if isinstance(node.value, ast.Call) and _dotted(node.value.func) in SAFE_TOP_CALLS:
                continue
        return False
    return True


def _modname(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _py_files() -> list[Path]:
    """What git tracks or would track (never what it ignores); outside a git repo, every file."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        paths = [ROOT / f for f in listed]
    except (OSError, subprocess.CalledProcessError):
        paths = list(ROOT.rglob("*.py"))
    return sorted(
        p for p in paths if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts)
    )


def _sandbox_env(tmp: Path) -> dict[str, str]:
    """Environment with fake keys: what is already set is not overwritten by the .env."""
    names = set()
    env_file = ROOT / ".env"
    if env_file.is_file():  # only the names, never the values
        names |= set(re.findall(r"^([A-Z0-9_]+)=", env_file.read_text(encoding="utf-8"), re.M))
    names |= {
        "ANTHROPIC_API_KEY",
        "APIFY_API_TOKEN",
        "APOLLO_API_KEY",
        "NANOGPT_API_KEY",
        "SERPER_API_KEY",
        "RESEND_API_KEY",
        "DATABASE_URL",
    }
    env = dict(os.environ)
    for name in names:
        if name.endswith("DATABASE_URL"):
            env[name] = f"sqlite:///{(tmp / (name.lower() + '.sqlite')).as_posix()}"
        elif name.endswith(("BASE_URL", "API_URL")):
            env[name] = "http://127.0.0.1:9"  # closed port: fails immediately
        elif re.search(r"(KEY|TOKEN|SECRET|PASSWORD)$", name):
            env[name] = "dummy"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


_IMPORT_CHILD = r"""
import importlib, json, sys
out = {}
for spec in sys.argv[1:]:
    mod, _, name = spec.partition(":")
    try:
        m = importlib.import_module(mod)
        if name:
            getattr(m, name)
    except BaseException as e:  # noqa: BLE001 - any failure is reported
        msg = (str(e).strip().splitlines() or [""])[0]
        out[spec] = f"{type(e).__name__}: {msg}"[:300]
print("__RESULT__" + json.dumps(out))
"""


def _run_imports(specs: list[str], env: dict[str, str]) -> dict[str, str]:
    if not specs:
        return {}
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_CHILD, *specs],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    marker = proc.stdout.rfind("__RESULT__")
    if marker < 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return {"<process>": f"rc={proc.returncode}: {tail}"}
    return json.loads(proc.stdout[marker + len("__RESULT__") :])


def _run_tool(args: list[str], env: dict[str, str]) -> dict[str, object]:
    proc = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900
    )
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return {"rc": proc.returncode, "summary": lines[-1] if lines else ""}


def verify() -> dict[str, object]:
    files = _py_files()
    result: dict[str, object] = {
        "compile_errors": {},
        "import_errors": {},
        "import_skipped": [],
        "help_errors": {},
        "help_ok": [],
        "cli_without_argparse": [],
        "tools": {},
    }
    import_specs: list[str] = []
    clis: list[Path] = []
    for path in files:
        rel = path.relative_to(ROOT)
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src, filename=str(rel))
            compile(tree, str(rel), "exec")
        except SyntaxError as e:
            result["compile_errors"][rel.as_posix()] = f"{e.msg} (line {e.lineno})"
            continue
        if _import_safe(tree):
            import_specs.append(_modname(rel))
        else:
            result["import_skipped"].append(rel.as_posix())
        if "__main__" in src:
            if "argparse" in src:
                clis.append(path)
            else:
                result["cli_without_argparse"].append(rel.as_posix())

    with tempfile.TemporaryDirectory(prefix="verify_") as tmp:
        env = _sandbox_env(Path(tmp))
        result["import_errors"] = _run_imports(import_specs, env)
        for path in clis:
            rel = path.relative_to(ROOT).as_posix()
            if any(word in path.name for word in NEVER_RUN):
                continue
            # Package modules are run with -m; standalone scripts, as a file.
            target = ["-m", _modname(path.relative_to(ROOT))] if rel.startswith("jobradar/") else [str(path)]
            try:
                proc = subprocess.run(
                    [sys.executable, *target, "--help"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                result["help_errors"][rel] = "timeout"
                continue
            if proc.returncode == 0 and "usage" in proc.stdout.lower():
                result["help_ok"].append(rel)
            else:
                tail = (proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines() or [""])[-1]
                result["help_errors"][rel] = f"rc={proc.returncode}: {tail}"[:300]
        if (ROOT / "tests").is_dir():
            result["tools"]["pytest"] = _run_tool([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], env)
        if (ROOT / "pyproject.toml").is_file():
            result["tools"]["ruff"] = _run_tool([sys.executable, "-m", "ruff", "check", "."], env)
    result["counts"] = {
        "files": len(files),
        "imported": len(import_specs),
        "clis": len(clis),
    }
    return result


def _failures(r: dict) -> set[str]:
    out = {f"compile:{k}" for k in r["compile_errors"]}
    out |= {f"import:{k}" for k in r["import_errors"]}
    out |= {f"help:{k}" for k in r["help_errors"]}
    out |= {f"tool:{k}" for k, v in r.get("tools", {}).items() if v["rc"] != 0}
    return out


def _detail(r: dict, key: str) -> str:
    kind, _, name = key.partition(":")
    table = {"compile": "compile_errors", "import": "import_errors", "help": "help_errors"}.get(kind)
    if table:
        return r[table].get(name, "")
    return str(r.get("tools", {}).get(name, {}).get("summary", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="JSON with the result")
    ap.add_argument("--compare", help="JSON from a previous run (baseline)")
    args = ap.parse_args()

    r = verify()
    Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    c = r["counts"]
    fails = _failures(r)
    print(f"files={c['files']} imported={c['imported']} clis={c['clis']} failures={len(fails)}")
    for name, tool in r["tools"].items():
        print(f"  {name}: rc={tool['rc']} {tool['summary']}")
    if not args.compare:
        for key in sorted(fails):
            print(f"  [base] {key}: {_detail(r, key)}")
        return 0
    base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    new, fixed = fails - _failures(base), _failures(base) - fails
    for key in sorted(new):
        print(f"  NEW  {key}: {_detail(r, key)}")
    for key in sorted(fixed):
        print(f"  FIXED {key}")
    print("OK: no new failures" if not new else f"NEW FAILURES: {len(new)}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
