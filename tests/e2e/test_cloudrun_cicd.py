"""Cloud Run CI/CD bundle: workflow lint, static rules, gate shell, setup script dry-run, docs.

Opt-in live check: HANDOFF_LIVE_URL=https://... uv run pytest tests/e2e/test_cloudrun_cicd.py -k live
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy-cloudrun.yml"
SETUP_SCRIPT = ROOT / "scripts/setup-cloudrun-cicd.sh"
DOC_GCP = ROOT / "docs/deploy-gcp.md"
FORBIDDEN = re.compile(
    r"secrets\.|--set-env-vars|--update-env-vars|--clear-env-vars|--remove-env-vars"
    r"|--set-secrets|--service-account|HANDOFF_AUTH_SECRET="
)
SHELL_TIMEOUT_S = 30


def _load() -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _steps() -> list[dict[str, Any]]:
    return _load()["jobs"]["deploy"]["steps"]


def _gate_script() -> str:
    gates = [s for s in _steps() if s.get("id") == "gate"]
    assert len(gates) == 1
    return gates[0]["run"]


def _run_gate(tmp_path: Path, env_vars: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], str]:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(out), **env_vars}
    proc = subprocess.run(
        ["bash", "-c", _gate_script()],
        env=env,
        capture_output=True,
        text=True,
        timeout=SHELL_TIMEOUT_S,
        check=False,
    )
    return proc, out.read_text(encoding="utf-8")


def test_actionlint_passes() -> None:
    exe = shutil.which("actionlint")
    if exe is None:
        pytest.skip(reason="actionlint not installed")
    proc = subprocess.run(
        [exe, str(WORKFLOW)], capture_output=True, text=True, timeout=SHELL_TIMEOUT_S, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_triggers_main_and_dispatch() -> None:
    data = _load()
    triggers = data["on"] if "on" in data else data[True]  # pyyaml parses bare `on` as True
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]


def test_no_secrets_or_env_flags() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert FORBIDDEN.findall(text) == []
    data = _load()
    assert data["permissions"]["id-token"] == "write"
    auth = [s for s in _steps() if str(s.get("uses", "")).startswith("google-github-actions/auth@")]
    assert len(auth) == 1
    assert auth[0]["with"]["workload_identity_provider"]
    assert auth[0]["with"]["service_account"]


def test_deploy_flags_match_service() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for flag in (
        "--port 7860",
        "--memory 1Gi",
        "--max-instances 1",
        "--min-instances 0",
        "--allow-unauthenticated",
        "--concurrency 80",
    ):
        assert flag in text, flag


def test_gate_skips_when_unset(tmp_path: Path) -> None:
    proc, output = _run_gate(tmp_path, {"PROJECT_ID": "", "WIF_PROVIDER": ""})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "enabled=false" in output.splitlines()


def test_gate_fails_when_partial(tmp_path: Path) -> None:
    proc, output = _run_gate(
        tmp_path,
        {"PROJECT_ID": "p", "WIF_PROVIDER": "w", "REGION": "", "DEPLOY_SA": "sa@p.iam"},
    )
    assert proc.returncode == 1
    assert "::error::" in proc.stdout + proc.stderr
    assert "enabled=true" not in output


def test_setup_script_dry_run() -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    ok = subprocess.run(
        ["bash", str(SETUP_SCRIPT)], env=env, capture_output=True, text=True, timeout=SHELL_TIMEOUT_S, check=False
    )
    assert ok.returncode == 0, ok.stderr
    for needle in (
        "workload-identity-pools create github",
        "service-accounts create handoff-deployer",
        "gh variable set GCP_WIF_PROVIDER",
    ):
        assert needle in ok.stdout, needle
    bad = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--bogus"],
        env=env,
        capture_output=True,
        text=True,
        timeout=SHELL_TIMEOUT_S,
        check=False,
    )
    assert bad.returncode == 2


def test_docs_mention_auto_deploy() -> None:
    text = DOC_GCP.read_text(encoding="utf-8")
    needles = (
        "deploy-cloudrun",
        "Workload Identity Federation",
        "GCP_WIF_PROVIDER",
        "scripts/setup-cloudrun-cicd.sh",
        "--port 7860",
        "--max-instances 1",
        "--memory 1Gi",
        "HANDOFF_AUTH_SECRET",
        "roles/run.builder",
    )
    for needle in needles:
        assert needle in text, needle


def test_live_health_and_env_name() -> None:
    url = os.environ.get("HANDOFF_LIVE_URL", "").rstrip("/")
    if not url:
        pytest.skip(reason="HANDOFF_LIVE_URL not set")
    assert httpx.get(f"{url}/health", timeout=SHELL_TIMEOUT_S, follow_redirects=True).status_code == 200
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        pytest.skip(reason="gcloud not installed")
    proc = subprocess.run(
        [
            gcloud, "run", "services", "describe", "handoff",
            "--region", "asia-northeast3",
            "--project", "illy-portfolio",
            "--format", "value(spec.template.spec.containers[0].env[].name)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0
    names = re.split(r"[;,\s]+", proc.stdout.strip())
    assert "HANDOFF_AUTH_SECRET" in names
