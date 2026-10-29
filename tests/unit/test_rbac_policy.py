import stat
from importlib import resources
from pathlib import Path

import pytest
from itsdangerous import TimestampSigner, URLSafeTimedSerializer

from handoff.config import Settings
from handoff.rbac.policy import (
    POLICY_DIR,
    ROUTE_SCOPES,
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    AuthConfig,
    build_auth,
    build_enforcer,
    is_allowed,
)
from handoff.rbac.tokens import (
    HOME_PATH,
    INVITE_SALT,
    ROLES,
    SECRET_FILE,
    InvalidInvite,
    Principal,
    issue_invite,
    load_secret,
    read_invite,
)

ALL = ("anonymous", "leaver", "successor", "manager")

# Role policy table: (method, template, scope, roles allowed). Public rows allow everyone.
TABLE: list[tuple[str, str, str, frozenset[str]]] = [
    ("GET", "/health", "public", frozenset(ALL)),
    ("GET", "/", "public", frozenset(ALL)),
    ("GET", "/chat", "public", frozenset(ALL)),
    ("GET", "/report", "public", frozenset(ALL)),
    ("GET", "/auth/accept", "public", frozenset(ALL)),
    ("GET", "/auth/me", "public", frozenset(ALL)),
    ("POST", "/auth/logout", "public", frozenset(ALL)),
    ("GET", "/question-trees/{role}", "none", frozenset({"leaver", "successor", "manager"})),
    ("POST", "/sessions", "body", frozenset({"leaver"})),
    ("GET", "/sessions/{session_id}", "session", frozenset({"leaver"})),
    ("POST", "/sessions/{session_id}/answers", "session", frozenset({"leaver"})),
    ("POST", "/sessions/{session_id}/end", "session", frozenset({"leaver"})),
    ("GET", "/coverage", "query", frozenset({"leaver", "manager"})),
    ("POST", "/kb/query", "none", frozenset()),
    ("POST", "/leavers/{leaver}/sources", "path", frozenset({"manager"})),
    ("POST", "/leavers/{leaver}/sources/mock", "path", frozenset({"manager"})),
    ("GET", "/leavers/{leaver}/sources", "path", frozenset({"leaver", "manager"})),
    ("GET", "/leavers/{leaver}/entities", "path", frozenset({"leaver", "manager"})),
    ("POST", "/leavers/{leaver}/questions/generate", "path", frozenset({"manager"})),
    ("POST", "/depth/judge", "none", frozenset({"leaver"})),
    ("POST", "/depth/follow-up", "none", frozenset({"leaver"})),
    ("POST", "/depth/tacit", "none", frozenset({"leaver"})),
    ("GET", "/sessions/{session_id}/depth", "session", frozenset({"leaver"})),
    ("GET", "/leavers/{leaver}/cards", "path", frozenset({"leaver", "successor"})),
    ("GET", "/leavers/{leaver}/cards/at-risk", "path", frozenset({"successor", "manager"})),
    ("POST", "/sessions/{session_id}/cards/extract", "session", frozenset({"leaver"})),
    ("POST", "/leavers/{leaver}/gaps/refresh", "path", frozenset({"manager"})),
    ("GET", "/leavers/{leaver}/gaps", "path", frozenset({"manager"})),
    ("GET", "/leavers/{leaver}/queue", "path", frozenset({"manager"})),
    ("POST", "/leavers/{leaver}/priority-questions", "path", frozenset({"manager"})),
    ("POST", "/kb/ask", "body", frozenset({"successor"})),
    (
        "GET",
        "/leavers/{leaver}/successor-questions",
        "path",
        frozenset({"successor", "manager"}),
    ),
    (
        "GET",
        "/leavers/{leaver}/questions/{qid}/thread",
        "path",
        frozenset({"successor", "manager"}),
    ),
    ("POST", "/leavers/{leaver}/artifacts/{kind}", "path", frozenset({"manager"})),
    (
        "GET",
        "/leavers/{leaver}/artifacts/{kind}",
        "path",
        frozenset({"leaver", "successor", "manager"}),
    ),
    ("POST", "/leavers/{leaver}/drills", "path", frozenset({"successor"})),
    ("GET", "/drills/{drill_id}", "drill", frozenset({"successor"})),
    ("POST", "/drills/{drill_id}/answers", "drill", frozenset({"successor"})),
    ("POST", "/kb/fence", "body", frozenset({"successor"})),
    ("POST", "/leavers/{leaver}/premortem/questions", "path", frozenset({"manager"})),
    ("GET", "/admin", "public", frozenset(ALL)),
    ("GET", "/admin/leavers", "none", frozenset({"admin"})),
    ("POST", "/admin/leavers", "none", frozenset({"admin"})),
    ("POST", "/admin/leavers/{leaver_id}/invites", "none", frozenset({"admin"})),
    ("GET", "/demo", "public", frozenset(ALL)),
    ("POST", "/demo/start", "public", frozenset(ALL)),
    ("POST", "/demo/role", "public", frozenset(ALL)),
    ("POST", "/demo/reset", "public", frozenset(ALL)),
    ("GET", "/demo/status", "public", frozenset(ALL)),
]

CASES = [
    pytest.param(method, path, role, role in allowed, id=f"{role}-{method}-{path}")
    for method, path, _scope, allowed in TABLE
    for role in ALL
]

SECRET = "unit-test-secret"


@pytest.fixture(scope="module")
def enforcer():  # type: ignore[no-untyped-def]
    return build_enforcer()


@pytest.mark.parametrize(("method", "path", "role", "expected"), CASES)
def test_policy_table(enforcer, method: str, path: str, role: str, expected: bool) -> None:  # type: ignore[no-untyped-def]
    assert is_allowed(enforcer, role, method, path) is expected


def test_route_scopes_match_table() -> None:
    assert ROUTE_SCOPES == {(m, p): scope for m, p, scope, _ in TABLE}


def test_head_is_treated_as_get(enforcer) -> None:  # type: ignore[no-untyped-def]
    assert is_allowed(enforcer, "anonymous", "HEAD", "/health") is True
    assert is_allowed(enforcer, "manager", "HEAD", "/leavers/{leaver}/queue") is True
    assert is_allowed(enforcer, "leaver", "HEAD", "/leavers/{leaver}/queue") is False


def test_unknown_route_and_role_denied(enforcer) -> None:  # type: ignore[no-untyped-def]
    assert is_allowed(enforcer, "manager", "GET", "/nope") is False
    assert is_allowed(enforcer, "intruder", "POST", "/sessions") is False
    assert is_allowed(enforcer, "intruder", "GET", "/health") is False


def test_policy_files_are_package_data() -> None:
    package = resources.files("handoff.rbac")
    assert package.joinpath("model.conf").is_file()
    assert package.joinpath("policy.csv").is_file()
    assert (POLICY_DIR / "model.conf").is_file()
    assert (POLICY_DIR / "policy.csv").is_file()
    assert "/kb/query" not in (POLICY_DIR / "policy.csv").read_text(encoding="utf-8")


def test_constants() -> None:
    assert SESSION_COOKIE == "handoff_session"
    assert SESSION_MAX_AGE_S == 7 * 24 * 3600
    assert ROLES == ("leaver", "successor", "manager")
    assert HOME_PATH == {
        "leaver": "/",
        "successor": "/chat",
        "manager": "/report",
        "admin": "/admin",
    }


def test_build_auth_off_returns_none(tmp_path: Path) -> None:
    settings = Settings(provider="fake", data_dir=tmp_path, min_score=0.25, collection="c")
    assert build_auth(settings) is None
    assert not (tmp_path / SECRET_FILE).exists()


def test_build_auth_on(tmp_path: Path) -> None:
    settings = Settings(
        provider="fake",
        data_dir=tmp_path,
        min_score=0.25,
        collection="c",
        auth=True,
        auth_secret="env-secret",
        invite_ttl_hours=2,
    )
    auth = build_auth(settings)
    assert isinstance(auth, AuthConfig)
    assert auth.secret == "env-secret"
    assert auth.invite_ttl_s == 7200
    assert is_allowed(auth.enforcer, "successor", "POST", "/kb/ask") is True


def test_auth_config_repr_hides_secret() -> None:
    config = AuthConfig(secret="very-secret-value", invite_ttl_s=60, enforcer=build_enforcer())
    assert "very-secret-value" not in repr(config)
    assert "invite_ttl_s=60" in repr(config)


# --- tokens ---


@pytest.mark.parametrize("role", ["leaver", "successor", "manager"])
def test_invite_round_trip(role: str) -> None:
    principal = Principal.model_validate({"role": role, "leaver": "chat1"})
    token = issue_invite(SECRET, principal)
    assert read_invite(SECRET, token, max_age_s=60) == principal


def test_invite_forged_secret_rejected() -> None:
    token = issue_invite(SECRET, Principal(role="manager", leaver="chat1"))
    with pytest.raises(InvalidInvite):
        read_invite("other-secret", token, max_age_s=60)


def test_invite_tampered_token_rejected() -> None:
    token = issue_invite(SECRET, Principal(role="leaver", leaver="chat1"))
    with pytest.raises(InvalidInvite):
        read_invite(SECRET, token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), max_age_s=60)
    with pytest.raises(InvalidInvite):
        read_invite(SECRET, "garbage", max_age_s=60)


def test_invite_wrong_salt_rejected() -> None:
    token = URLSafeTimedSerializer(SECRET, salt="other").dumps(
        {"role": "leaver", "leaver": "chat1"}
    )
    with pytest.raises(InvalidInvite):
        read_invite(SECRET, token, max_age_s=60)


class _PastSigner(TimestampSigner):
    def get_timestamp(self) -> int:
        return 1_000_000_000


class _PastSerializer(URLSafeTimedSerializer):
    # The timestamp is taken by the signer, so the past clock lives there.
    default_signer = _PastSigner


def test_invite_expired_rejected() -> None:
    token = _PastSerializer(SECRET, salt=INVITE_SALT).dumps({"role": "leaver", "leaver": "chat1"})
    with pytest.raises(InvalidInvite):
        read_invite(SECRET, token, max_age_s=60)


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "admin", "leaver": "chat1"},
        {"role": "leaver", "leaver": "Chat 1"},
        {"role": "leaver", "leaver": ""},
        {"role": "leaver", "leaver": "a" * 41},
        {"role": "leaver"},
        ["leaver", "chat1"],
    ],
)
def test_invite_invalid_payload_rejected(payload: object) -> None:
    token = URLSafeTimedSerializer(SECRET, salt=INVITE_SALT).dumps(payload)
    with pytest.raises(InvalidInvite):
        read_invite(SECRET, token, max_age_s=60)


def test_load_secret_creates_file_with_0600(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"
    secret = load_secret(data_dir)
    path = data_dir / SECRET_FILE
    assert path.is_file()
    assert len(secret) >= 32
    assert path.read_text(encoding="utf-8").strip() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_secret_reuses_existing_file(tmp_path: Path) -> None:
    first = load_secret(tmp_path)
    assert load_secret(tmp_path) == first


def test_load_secret_env_wins(tmp_path: Path) -> None:
    load_secret(tmp_path)
    assert load_secret(tmp_path, "  from-env  ") == "from-env"
    assert load_secret(tmp_path, "   ") != "from-env"


def test_load_secret_env_does_not_create_file(tmp_path: Path) -> None:
    assert load_secret(tmp_path, "from-env") == "from-env"
    assert not (tmp_path / SECRET_FILE).exists()
