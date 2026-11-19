"""Hard rule: the code that decides truth never imports the AI.

Nothing under app/rules, app/finance, app/tracker, app/guard may import `anthropic` or anything from
`app.brain` (absolute or relative).
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
GUARDED = ["rules", "finance", "tracker", "guard", "signin"]


def imported_modules(path: Path) -> list[str]:
    """Absolute module names imported by a file (relative imports resolved against its package)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(path.relative_to(APP.parent).with_suffix("").parts[:-1])
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            names.append(mod)
            names += [f"{mod}.{a.name}" for a in node.names]
    return names


def forbidden(name: str) -> bool:
    return name in ("anthropic", "app.brain") or name.startswith(("anthropic.", "app.brain."))


FILES = [p for d in GUARDED for p in sorted((APP / d).rglob("*.py"))]


def test_guarded_packages_exist() -> None:
    assert all((APP / d).is_dir() for d in GUARDED) and FILES


@pytest.mark.parametrize("path", FILES, ids=[str(p.relative_to(APP)) for p in FILES])
def test_no_ai_imports(path: Path) -> None:
    bad = [n for n in imported_modules(path) if forbidden(n)]
    assert not bad, f"{path.relative_to(APP)} imports {bad}"


def test_detector_catches_violations(tmp_path: Path) -> None:
    pkg = APP / "rules"
    probe = pkg / "_probe_tmp.py"
    probe.write_text("import anthropic\nfrom ..brain import runner\nfrom app.brain.claude import x\n")
    try:
        assert [n for n in imported_modules(probe) if forbidden(n)] == [
            "anthropic",
            "app.brain",
            "app.brain.runner",
            "app.brain.claude",
            "app.brain.claude.x",
        ]
    finally:
        probe.unlink()
