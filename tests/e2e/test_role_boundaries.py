"""Role boundary E2E: an auth-on server, invite links from the CLI, httpx clients per role."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-secret"
ROLE = "backend_developer"
KIM_ANSWER = (
    "결제 대행사 장애가 나면 먼저 대행사 상태 페이지를 확인하고, "
    "담당 엔지니어에게 010-1234-5678 로 전화합니다."
)
KIM_QUESTION = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
LEE_ANSWER = "물류 창고 재고 동기화는 매시 정각에 돌고, 실패하면 재고 담당 채널에 알립니다."
LEE_QUESTION = "물류 창고 재고 동기화는 언제 도나요?"
ANSWER_FOLLOW = "정산 배치는 매일 새벽 2시에 돌고, 실패하면 아침 9시 전에 수동으로 다시 돌립니다."

StartServer = Callable[..., Any]


@dataclass
class World:
    url: str
    data_dir: Path

    def invite(self, role: str, leaver: str) -> str:
        env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
        env.update(
            {
                "HANDOFF_DATA_DIR": str(self.data_dir),
                "HANDOFF_AUTH": "on",
                "HANDOFF_AUTH_SECRET": SECRET,
                "HANDOFF_PROVIDER": "fake",
            }
        )
        out = subprocess.run(
            [sys.executable, "-m", "handoff.rbac.cli", "invite", "--role", role,
             "--leaver", leaver, "--base-url", self.url],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=20)

    def login(self, role: str, leaver: str) -> httpx.Client:
        c = self.client()
        resp = c.get(self.invite(role, leaver))
        assert resp.status_code == 303, resp.text
        return c


@pytest.fixture
def world(start_server: StartServer, tmp_path: Path) -> World:
    data_dir = tmp_path / "data"
    server = start_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    return World(server.url, data_dir)


@pytest.fixture
def clients(world: World) -> Iterator[dict[str, httpx.Client]]:
    made: dict[str, httpx.Client] = {}

    def add(name: str, role: str, leaver: str) -> None:
        made[name] = world.login(role, leaver)

    add("kim_leaver", "leaver", "kim")
    add("lee_leaver", "leaver", "lee")
    add("kim_successor", "successor", "kim")
    add("lee_successor", "successor", "lee")
    add("kim_manager", "manager", "kim")
    try:
        yield made
    finally:
        for c in made.values():
            c.close()


def _ok(resp: httpx.Response, code: int = 200) -> Any:
    assert resp.status_code == code, resp.text
    return resp.json() if resp.content else None


def _session(c: httpx.Client, leaver: str, answers: list[str], end: bool = True) -> str:
    sid = str(_ok(c.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)["session_id"])
    for text in answers:
        _ok(c.post(f"/sessions/{sid}/answers", json={"text": text}))
    if end:
        _ok(c.post(f"/sessions/{sid}/end"))
    return sid


def test_no_cookie_is_401(world: World) -> None:
    with world.client() as c:
        resp = c.get("/coverage", params={"leaver": "kim"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "login required"
        assert c.post("/sessions", json={"role": ROLE, "leaver": "kim"}).status_code == 401
        assert c.post("/kb/ask", json={"question": KIM_QUESTION, "leaver": "kim"}).status_code == 401


def test_forged_or_tampered_invite_is_401(world: World) -> None:
    good = world.invite("manager", "kim")
    with world.client() as c:
        forged = c.get("/auth/accept", params={"token": "not-a-real-token"})
        assert forged.status_code == 401
        assert forged.json()["detail"] == "invalid invite"
        tampered = c.get(good[:-3] + ("aaa" if not good.endswith("aaa") else "bbb"))
        assert tampered.status_code == 401
        assert c.get("/auth/me").json() == {"auth": True, "role": None, "leaver": None}


def test_auth_me_before_and_after_accept_and_logout(world: World) -> None:
    with world.client() as c:
        assert c.get("/auth/me").json() == {"auth": True, "role": None, "leaver": None}
        accepted = c.get(world.invite("successor", "kim"))
        assert accepted.status_code == 303
        assert accepted.headers["location"] == "/chat"
        assert c.get("/auth/me").json() == {"auth": True, "role": "successor", "leaver": "kim"}
        assert c.post("/auth/logout").status_code == 204
        assert c.get("/auth/me").json() == {"auth": True, "role": None, "leaver": None}
        after = c.post("/kb/ask", json={"question": KIM_QUESTION, "leaver": "kim"})
        assert after.status_code == 401


def test_invite_redirects_each_role_home(world: World) -> None:
    for role, home in (("leaver", "/"), ("successor", "/chat"), ("manager", "/report")):
        with world.client() as c:
            resp = c.get(world.invite(role, "kim"))
            assert resp.status_code == 303
            assert resp.headers["location"] == home


def test_successor_cannot_read_manager_gaps(clients: dict[str, httpx.Client]) -> None:
    s = clients["kim_successor"]
    assert s.get("/leavers/kim/gaps").status_code == 403
    assert s.get("/leavers/kim/queue").status_code == 403
    assert s.post("/leavers/kim/priority-questions", json={"text": "x"}).status_code == 403
    assert s.get("/coverage", params={"leaver": "kim"}).status_code == 403


def test_manager_cannot_read_other_leaver(clients: dict[str, httpx.Client]) -> None:
    m = clients["kim_manager"]
    assert m.get("/coverage", params={"leaver": "kim"}).status_code == 200
    resp = m.get("/coverage", params={"leaver": "lee"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "forbidden"
    assert m.get("/leavers/lee/queue").status_code == 403
    assert m.post("/leavers/lee/sources/mock").status_code == 403


def test_manager_cannot_see_raw_answers_depth_cards_or_drills(
    clients: dict[str, httpx.Client],
) -> None:
    sid = _session(clients["kim_leaver"], "kim", [KIM_ANSWER, ANSWER_FOLLOW])
    s = clients["kim_successor"]
    cards = _ok(s.get("/leavers/kim/cards"))
    assert cards, "successor should see kim's cards after a finished session"
    drill = _ok(s.post("/leavers/kim/drills", json={}), 201)
    drill_id = drill["id"]

    m = clients["kim_manager"]
    assert m.get(f"/sessions/{sid}").status_code == 403
    assert m.get(f"/sessions/{sid}/depth").status_code == 403
    assert m.get("/leavers/kim/cards").status_code == 403
    assert m.get(f"/drills/{drill_id}").status_code == 403
    assert m.post(f"/drills/{drill_id}/answers", json={"text": "x"}).status_code == 403
    assert m.post("/sessions", json={"role": ROLE, "leaver": "kim"}).status_code == 403


def test_leaver_cannot_touch_other_leaver_session(clients: dict[str, httpx.Client]) -> None:
    lee_sid = _session(clients["lee_leaver"], "lee", [], end=False)
    kim = clients["kim_leaver"]
    assert kim.post(f"/sessions/{lee_sid}/answers", json={"text": KIM_ANSWER}).status_code == 403
    assert kim.get(f"/sessions/{lee_sid}").status_code == 403
    assert kim.post(f"/sessions/{lee_sid}/end").status_code == 403
    assert kim.post("/sessions", json={"role": ROLE, "leaver": "lee"}).status_code == 403
    # lee's own session is untouched and still open.
    assert _ok(clients["lee_leaver"].get(f"/sessions/{lee_sid}"))["status"] == "open"


def test_kb_query_closed_for_every_role(clients: dict[str, httpx.Client], world: World) -> None:
    for c in clients.values():
        assert c.post("/kb/query", json={"question": KIM_QUESTION}).status_code == 403
    with world.client() as anon:
        assert anon.post("/kb/query", json={"question": KIM_QUESTION}).status_code == 401


def test_successor_cannot_ask_for_other_leaver(clients: dict[str, httpx.Client]) -> None:
    s = clients["kim_successor"]
    assert s.post("/kb/ask", json={"question": LEE_QUESTION, "leaver": "lee"}).status_code == 403
    assert s.post("/kb/fence", json={"subject": "x", "leaver": "lee"}).status_code == 403
    assert s.get("/leavers/lee/cards").status_code == 403


def test_kb_answers_do_not_mix_leavers(clients: dict[str, httpx.Client]) -> None:
    _session(clients["kim_leaver"], "kim", [KIM_ANSWER])
    _session(clients["lee_leaver"], "lee", [LEE_ANSWER])

    kim_s = clients["kim_successor"]
    lee_s = clients["lee_successor"]

    own = kim_s.post("/kb/ask", json={"question": KIM_QUESTION, "leaver": "kim"})
    assert _ok(own)["answer"]["found"] is True

    cross = kim_s.post("/kb/ask", json={"question": LEE_QUESTION, "leaver": "kim"})
    body = _ok(cross)
    # The question itself may be echoed back as a queued item; lee's answer must not be.
    assert "매시 정각" not in cross.text
    assert not any("물류" in c["excerpt"] for c in body["answer"]["citations"])
    assert "물류" not in body["answer"]["answer"]

    lee_own = lee_s.post("/kb/ask", json={"question": LEE_QUESTION, "leaver": "lee"})
    assert _ok(lee_own)["answer"]["found"] is True
    assert "매시 정각" in lee_own.text

    lee_cross = lee_s.post("/kb/ask", json={"question": KIM_QUESTION, "leaver": "lee"})
    _ok(lee_cross)
    assert "결제 대행사 장애가 나면 먼저" not in lee_cross.text
    assert "상태 페이지" not in lee_cross.text


def test_kb_answer_hides_contact_details(clients: dict[str, httpx.Client]) -> None:
    _session(clients["kim_leaver"], "kim", [KIM_ANSWER])
    resp = clients["kim_successor"].post(
        "/kb/ask", json={"question": KIM_QUESTION, "leaver": "kim"}
    )
    body = _ok(resp)
    assert body["answer"]["found"] is True
    assert "010-" not in resp.text
    assert "1234-5678" not in resp.text
    assert "[전화번호]" in body["answer"]["answer"]
    assert any("[전화번호]" in c["excerpt"] for c in body["answer"]["citations"])
    assert body["certainty"] in ("certain", "uncertain")


def test_other_role_pages_show_forbidden_message_flow(clients: dict[str, httpx.Client]) -> None:
    # The pages themselves are public; the API calls they make are what gets blocked.
    s = clients["kim_successor"]
    assert s.get("/report").status_code == 200
    assert _ok(s.get("/auth/me"))["role"] == "successor"
    assert s.get("/coverage", params={"leaver": "kim", "role": ROLE}).status_code == 403
    m = clients["kim_manager"]
    assert m.get("/chat").status_code == 200
    assert m.post("/kb/ask", json={"question": KIM_QUESTION, "leaver": "kim"}).status_code == 403
