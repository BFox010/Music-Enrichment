"""Ratchet on over-long comment blocks (CLAUDE.md → Comments).

Ceilings: 4 lines for an inline ``#``/``//`` block, 8 for a function or class
docstring, 12 for a module docstring. A file may not gain over-ceiling blocks
beyond its baseline, and a file absent from the baseline may have none. When a
file drops below its baseline, lower the entry — the baseline only falls.

Regenerate after a deliberate cleanup:
    python -m tests.test_comment_ceilings --write
"""

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).with_name("comment_ceiling_baseline.json")

INLINE_MAX, FUNC_DOC_MAX, MODULE_DOC_MAX = 4, 8, 12

_GLOBS = ("pipeline/*.py", "app/*.py", "scripts/*.py", "scripts/*.mjs",
          "web/*.js", "web/*.jsx")
_EXCLUDE = re.compile(r"(app\.bundle\.js|\.test\.js)$")


def _files() -> list[Path]:
    return sorted(p for g in _GLOBS for p in REPO_ROOT.glob(g)
                  if not _EXCLUDE.search(p.name))


def _comment_runs(lines: list[str], prefix: str) -> list[int]:
    runs, n = [], 0
    for ln in lines:
        if ln.strip().startswith(prefix):
            n += 1
        elif n:
            runs.append(n)
            n = 0
    return runs + ([n] if n else [])


def _over_python(src: str) -> int:
    lines = src.splitlines()
    # Standalone comment lines only; a shebang or encoding line is not prose.
    over = sum(r > INLINE_MAX for r in _comment_runs(
        [ln for ln in lines if not ln.startswith("#!")], "#"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                cap = MODULE_DOC_MAX if isinstance(node, ast.Module) else FUNC_DOC_MAX
                over += doc.count("\n") + 1 > cap
    return over


def _over_js(src: str) -> int:
    blocks = re.findall(r"/\*.*?\*/", src, re.S)
    over = sum(b.count("\n") + 1 > INLINE_MAX for b in blocks)
    stripped = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return over + sum(r > INLINE_MAX for r in _comment_runs(stripped.splitlines(), "//"))


def measure() -> dict[str, int]:
    counts = {}
    for path in _files():
        src = path.read_text(encoding="utf-8")
        n = _over_python(src) if path.suffix == ".py" else _over_js(src)
        if n:
            counts[path.relative_to(REPO_ROOT).as_posix()] = n
    return counts


def test_no_file_gains_over_ceiling_comment_blocks():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    grown = {f: (baseline.get(f, 0), n) for f, n in measure().items()
             if n > baseline.get(f, 0)}
    assert not grown, (
        "Comment blocks over the ceiling (inline 4 / docstring 8 / module 12 lines) "
        "grew; compress them to the failure and the forbidden change. "
        "{file: (baseline, now)}: " + repr(grown))


def test_baseline_has_no_slack():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    now = measure()
    slack = {f: (n, now.get(f, 0)) for f, n in baseline.items() if now.get(f, 0) < n}
    assert not slack, (
        "Files now have fewer over-ceiling blocks than the baseline; lower it with "
        "`python -m tests.test_comment_ceilings --write`. {file: (baseline, now)}: "
        + repr(slack))


if __name__ == "__main__" and "--write" in sys.argv:
    BASELINE_PATH.write_text(json.dumps(measure(), indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
