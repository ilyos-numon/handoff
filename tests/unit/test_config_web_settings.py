from pathlib import Path

import pytest

from handoff.config import DEFAULT_WEB_DIST, Settings, api_docs_kwargs, load_settings


def test_web_dist_defaults_to_repo_web_dist() -> None:
    assert load_settings({}).web_dist == DEFAULT_WEB_DIST
    assert DEFAULT_WEB_DIST.parts[-2:] == ("web", "dist")


def test_web_dist_off_is_none() -> None:
    assert load_settings({"HANDOFF_WEB_DIST": "off"}).web_dist is None


def test_web_dist_custom_path() -> None:
    assert load_settings({"HANDOFF_WEB_DIST": "/srv/web"}).web_dist == Path("/srv/web")


def test_web_dist_dataclass_default_is_none() -> None:
    direct = Settings(provider="fake", data_dir=Path("./data"), min_score=0.25, collection="c")
    assert direct.web_dist is None
    assert direct.trusted_proxies == ()
    assert direct.api_docs is True


@pytest.mark.parametrize("env", [{}, {"HANDOFF_TRUSTED_PROXIES": ""}])
def test_trusted_proxies_empty(env: dict[str, str]) -> None:
    assert load_settings(env).trusted_proxies == ()


def test_trusted_proxies_split_strip_drop_empty() -> None:
    settings = load_settings({"HANDOFF_TRUSTED_PROXIES": "a, b,,"})
    assert settings.trusted_proxies == ("a", "b")


def test_api_docs_default_off_when_auth_on() -> None:
    assert load_settings({}).api_docs is False


def test_api_docs_default_on_when_auth_off() -> None:
    assert load_settings({"HANDOFF_AUTH": "off"}).api_docs is True


def test_api_docs_explicit_override() -> None:
    assert load_settings({"HANDOFF_API_DOCS": "on"}).api_docs is True
    assert load_settings({"HANDOFF_AUTH": "off", "HANDOFF_API_DOCS": "off"}).api_docs is False


def test_api_docs_invalid_value() -> None:
    with pytest.raises(ValueError, match="HANDOFF_API_DOCS must be on or off"):
        load_settings({"HANDOFF_API_DOCS": "maybe"})


def test_api_docs_kwargs_on() -> None:
    assert api_docs_kwargs(load_settings({"HANDOFF_API_DOCS": "on"})) == {}


def test_api_docs_kwargs_off() -> None:
    assert api_docs_kwargs(load_settings({"HANDOFF_API_DOCS": "off"})) == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
