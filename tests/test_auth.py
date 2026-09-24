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


def test_session_cookie_survives_requests_to_any_slack_host():
    # Un en-tête Cookie brut est supprimé par requests à chaque redirection ;
    # le cookie doit être dans le cookie jar, rattaché au domaine slack.com.
    import requests

    creds = credentials_from_env({"SLACK_TOKEN": "xoxc-123", "SLACK_COOKIE_D": "xoxd-abc"})
    session = build_api(creds).session

    assert "Cookie" not in session.headers
    for url in ("https://files.slack.com/files-pri/T1-F1/a.pdf", "https://acme.slack.com/x"):
        prepared = session.prepare_request(requests.Request("GET", url))
        assert prepared.headers["Cookie"] == "d=xoxd-abc"


def test_rate_limit_handler_announces_wait_before_sleeping(monkeypatch):
    from slack_sdk.http_retry import HttpRequest, HttpResponse, RetryState
    from slack_sdk.http_retry import builtin_handlers

    from slack_exporter.auth import NotifyingRateLimitHandler

    events = []
    monkeypatch.setattr(builtin_handlers.time, "sleep", lambda s: events.append(("sleep", int(s))))
    handler = NotifyingRateLimitHandler(notify=lambda m: events.append(("notify", m)), max_retry_count=5)

    handler.prepare_for_next_attempt(
        state=RetryState(),
        request=HttpRequest(method="POST", url="https://slack.com/api/users.conversations", headers={}),
        response=HttpResponse(status_code=429, headers={"Retry-After": ["30"]}),
    )

    assert events == [("notify", "Limite Slack atteinte, reprise dans 30 s…"), ("sleep", 30)]


def test_build_api_announces_rate_limits_through_notify():
    from slack_exporter.auth import NotifyingRateLimitHandler

    notices = []
    api = build_api(credentials_from_env({"SLACK_TOKEN": "xoxp-123"}), notify=notices.append)

    handlers = [h for h in api.client.retry_handlers if isinstance(h, NotifyingRateLimitHandler)]
    assert len(handlers) == 1
    handlers[0].notify("test")
    assert notices == ["test"]
