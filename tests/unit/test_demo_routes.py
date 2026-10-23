"""Demo API (/demo/*) wired into the full app with the fake provider."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.config import Settings
from handoff.demo.routes import (
    DEMO_BUSY,
    DEMO_OFF,
    DEMO_SEED_FAILED,
    DEMO_SESSION_REQUIRED,
    DemoRuntime,
)
from handoff.demo.sandbox import SEED_ROLE
from handoff.main import create_app
from handoff.rbac.policy import SESSION_COOKIE

SECRET = "t"


def _settings(data_dir: Path, *, demo: bool = True, max_live: int = 5) -> Settings:
    return Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=True,
        auth_secret=SECRET,
        demo=demo,
        demo_max_sandboxes=max_live,
    )


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    return create_app(_settings(tmp_path))


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _start(client: TestClient) -> dict[str, Any]:
    res = client.post("/demo/start")
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _has_data(app: FastAPI, leaver: str) -> bool:
    ks = app.state.knowledge.store
    return bool(
        ks.list_queue(leaver)
        or ks.list_sources(leaver)
        or app.state.services.sessions.coverage(leaver, SEED_ROLE).items_total
        or app.state.services.kb.query(
            "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?", leaver=leaver
        ).found
    )


def test_demo_off_returns_404_and_status_false(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo=False))) as c:
        assert c.app.state.knowledge.demo is None  # type: ignore[attr-defined]
        for method, path in (
            ("GET", "/demo"),
            ("POST", "/demo/start"),
            ("POST", "/demo/reset"),
        ):
            res = c.request(method, path)
            assert res.status_code == 404, path
            assert res.json() == {"detail": DEMO_OFF}
        res = c.post("/demo/role", json={"role": "manager"})
        assert res.status_code == 404
        assert c.get("/demo/status").json() == {
            "demo": False,
            "leaver": None,
            "role": None,
            "expires_at": None,
            "llm": None,
        }


def test_start_sets_cookie_seeds_and_is_idempotent(client: TestClient, app: FastAPI) -> None:
    body = _start(client)
    assert body["leaver"].startswith("demo-")
    assert body["role"] == "manager"
    assert body["expires_at"]
    assert client.cookies.get(SESSION_COOKIE)
    assert _has_data(app, body["leaver"])
    assert _start(client)["leaver"] == body["leaver"]
    runtime: DemoRuntime = app.state.knowledge.demo
    assert runtime.sandboxes.live_count() == 1
    me = client.get("/auth/me").json()
    assert me == {"auth": True, "role": "manager", "leaver": body["leaver"]}


@pytest.mark.parametrize(
    ("role", "home"), [("leaver", "/"), ("successor", "/chat"), ("manager", "/report")]
)
def test_role_switch_updates_principal(client: TestClient, role: str, home: str) -> None:
    leaver = _start(client)["leaver"]
    res = client.post("/demo/role", json={"role": role})
    assert res.status_code == 200
    assert res.json() == {"role": role, "home": home}
    assert client.get("/auth/me").json() == {"auth": True, "role": role, "leaver": leaver}


def test_successor_after_switch_can_ask(client: TestClient) -> None:
    leaver = _start(client)["leaver"]
    client.post("/demo/role", json={"role": "successor"})
    res = client.post("/kb/ask", json={"question": "정산 배치는 언제 도나요?", "leaver": leaver})
    assert res.status_code == 200


def test_role_without_sandbox_is_401(client: TestClient) -> None:
    res = client.post("/demo/role", json={"role": "successor"})
    assert res.status_code == 401
    assert res.json() == {"detail": DEMO_SESSION_REQUIRED}
    assert client.post("/demo/reset").status_code == 401


@pytest.mark.parametrize("role", ["admin", "intruder", ""])
def test_role_invalid_is_422(client: TestClient, role: str) -> None:
    _start(client)
    assert client.post("/demo/role", json={"role": role}).status_code == 422


def test_reset_gives_new_id_and_purges_old(client: TestClient, app: FastAPI) -> None:
    old = _start(client)["leaver"]
    client.post("/demo/role", json={"role": "successor"})
    res = client.post("/demo/reset")
    assert res.status_code == 200
    new = res.json()
    assert new["leaver"] != old
    assert new["role"] == "manager"
    assert not _has_data(app, old)
    assert _has_data(app, new["leaver"])
    runtime: DemoRuntime = app.state.knowledge.demo
    assert runtime.sandboxes.get(old) is None
    assert runtime.sandboxes.live_count() == 1
    assert client.get("/auth/me").json()["leaver"] == new["leaver"]


def test_status_without_and_with_cookie(client: TestClient) -> None:
    before = client.get("/demo/status").json()
    assert before == {"demo": True, "leaver": None, "role": None, "expires_at": None, "llm": None}
    body = _start(client)
    after = client.get("/demo/status").json()
    assert after["demo"] is True
    assert after["leaver"] == body["leaver"]
    assert after["role"] == "manager"
    assert after["expires_at"] == body["expires_at"]


def test_status_reports_llm_stats(client: TestClient, app: FastAPI) -> None:
    stats = {"hits": 1, "misses": 0, "live_calls_today": 0, "live_cap": 3, "fake_fallbacks": 0}
    app.state.knowledge.demo.llm_stats = lambda: stats
    assert client.get("/demo/status").json()["llm"] == stats


def test_max_sandboxes_returns_503(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, max_live=1))
    with TestClient(app) as a:
        _start(a)
        b = TestClient(app)
        res = b.post("/demo/start")
        assert res.status_code == 503
        assert res.json() == {"detail": DEMO_BUSY}
        assert b.get("/demo/status").json()["leaver"] is None


def test_seed_failure_returns_503_and_rolls_back(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("seed broke")

    monkeypatch.setattr("handoff.demo.routes.seed_scenario", boom)
    res = client.post("/demo/start")
    assert res.status_code == 503
    assert res.json() == {"detail": DEMO_SEED_FAILED}
    assert app.state.knowledge.demo.sandboxes.live_count() == 0


def test_other_visitor_cookie_cannot_read_first_sandbox(client: TestClient, app: FastAPI) -> None:
    a_leaver = _start(client)["leaver"]
    assert client.get(f"/leavers/{a_leaver}/queue").status_code == 200
    b = TestClient(app)
    b_leaver = _start(b)["leaver"]
    assert b_leaver != a_leaver
    assert b.get(f"/leavers/{a_leaver}/queue").status_code == 403
    assert b.get(f"/leavers/{b_leaver}/queue").status_code == 200


def test_expired_demo_cookie_is_ignored(client: TestClient, app: FastAPI) -> None:
    leaver = _start(client)["leaver"]
    runtime: DemoRuntime = app.state.knowledge.demo
    box = runtime.sandboxes.get(leaver)
    assert box is not None
    runtime.sandboxes._clock = lambda: box.expires_at
    assert client.post("/demo/role", json={"role": "successor"}).status_code == 401
    assert client.get("/demo/status").json()["leaver"] is None
