import pytest

from slack_exporter.auth import ConfigError, build_api, credentials_from_env


def test_user_token_is_accepted():
    creds = credentials_from_env({"SLACK_TOKEN": "xoxp-123"})
    assert creds.token == "xoxp-123"
    assert creds.extra_headers() == {}


def test_browser_token_requires_cookie():
    with pytest.raises(ConfigError, match="SLACK_COOKIE_D"):
        credentials_from_env({"SLACK_TOKEN": "xoxc-123"})


def test_browser_token_with_cookie():
    creds = credentials_from_env({"SLACK_TOKEN": "xoxc-123", "SLACK_COOKIE_D": "xoxd-abc"})
    assert creds.extra_headers() == {"Cookie": "d=xoxd-abc"}


def test_missing_token():
    with pytest.raises(ConfigError, match="SLACK_TOKEN"):
        credentials_from_env({})


def test_bot_token_is_rejected():
    with pytest.raises(ConfigError, match="xoxp"):
        credentials_from_env({"SLACK_TOKEN": "xoxb-123"})


def test_repr_does_not_leak_secrets():
    creds = credentials_from_env({"SLACK_TOKEN": "xoxc-secret", "SLACK_COOKIE_D": "xoxd-secret"})
    assert "secret" not in repr(creds)


def test_build_api_sets_auth_headers_on_client_and_session():
    creds = credentials_from_env({"SLACK_TOKEN": "xoxc-123", "SLACK_COOKIE_D": "xoxd-abc"})
    api = build_api(creds)

    assert api.client.token == "xoxc-123"
    assert api.client.headers["Cookie"] == "d=xoxd-abc"
    assert api.session.headers["Authorization"] == "Bearer xoxc-123"
    assert api.session.headers["Cookie"] == "d=xoxd-abc"
