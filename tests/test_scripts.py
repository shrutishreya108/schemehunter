"""scripts/check_secrets.sh, scripts/estimate_costs.py, scripts/deploy.sh sanity. No services needed."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_secrets.sh"
FAKE_KEY = "sk-" + "ant-" + "api03-" + "Z" * 40  # built at runtime so this file never contains a key


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(CHECK), str(tree)], check=False, capture_output=True, text=True)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ok.py").write_text("print('hello')\n")
    (tmp_path / ".env.example").write_text("SMTP_PASSWORD=\nANTHROPIC_API_KEY=\n")
    return tmp_path


def test_clean_tree_passes(tree: Path) -> None:
    r = run_check(tree)
    assert r.returncode == 0 and "ok" in r.stdout


def test_anthropic_key_in_code_fails(tree: Path) -> None:
    (tree / "app" / "leak.py").write_text(f'KEY = "{FAKE_KEY}"\n')
    r = run_check(tree)
    assert r.returncode == 1 and "leak.py" in r.stdout


def test_smtp_password_value_in_tree_fails(tree: Path) -> None:
    (tree / ".env").write_text("SMTP_PASSWORD=Titan#Pass-2026\nAPP_BASE_URL=https://example.in\n")
    (tree / "app" / "settings_copy.txt").write_text("password is Titan#Pass-2026\n")
    r = run_check(tree)
    assert r.returncode == 1 and "SMTP_PASSWORD" in r.stdout


def test_env_values_only_in_env_pass(tree: Path) -> None:
    (tree / ".env").write_text(f"SMTP_PASSWORD=Titan#Pass-2026\nANTHROPIC_API_KEY={FAKE_KEY}\n")
    assert run_check(tree).returncode == 0  # .env itself is excluded


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_tracked_env_fails(tree: Path) -> None:
    (tree / ".env").write_text("X=1\n")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    subprocess.run(["git", "add", "-f", ".env"], cwd=tree, check=True)
    r = run_check(tree)
    assert r.returncode == 1 and "tracked by git" in r.stdout


def test_repo_itself_is_clean() -> None:
    r = subprocess.run(["sh", str(CHECK)], check=False, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout


def test_estimate_costs_runs_and_fits_budget() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "estimate_costs.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "One full persona journey" in r.stdout and "6-minute demo script" in r.stdout
    sys.path.insert(0, str(ROOT / "scripts"))
    import estimate_costs as ec

    journey = sum(c.cost for c in ec.journey("claude-sonnet-4-5", "claude-haiku-4-5"))
    demo = sum(c.cost for c in ec.demo("claude-sonnet-4-5", "claude-haiku-4-5"))
    assert 0 < demo < journey < 2.0  # a journey must stay far below the $10/day guard


def test_deploy_script_refuses_without_target() -> None:
    r = subprocess.run(
        ["sh", str(ROOT / "scripts" / "deploy.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert r.returncode != 0 and "VPS" in r.stderr
