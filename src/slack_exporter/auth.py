"""Lecture des identifiants Slack et construction du client."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

import requests
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
    ServerErrorRetryHandler,
)

from slack_exporter.slack_api import MAX_ATTEMPTS, SlackApi, rate_limit_notice


class NotifyingRateLimitHandler(RateLimitErrorRetryHandler):
    """Comme RateLimitErrorRetryHandler, mais annonce la pause avant d'attendre."""

    def __init__(self, notify: Callable[[str], None], max_retry_count: int) -> None:
        super().__init__(max_retry_count=max_retry_count)
        self.notify = notify

    def prepare_for_next_attempt(self, *, state, request, response=None, error=None) -> None:
        if response is not None:
            retry_after = next(
                (v for k, v in response.headers.items() if k.lower() == "retry-after"), None
            )
            value = retry_after[0] if isinstance(retry_after, list) else retry_after
            if value is not None and str(value).isdigit():
                self.notify(rate_limit_notice(int(value)))
        super().prepare_for_next_attempt(state=state, request=request, response=response, error=error)


class ConfigError(Exception):
    """Identifiants absents ou invalides."""


@dataclass(frozen=True)
class Credentials:
    token: str = field(repr=False)
    cookie_d: str | None = field(default=None, repr=False)

    def extra_headers(self) -> dict[str, str]:
        return {"Cookie": f"d={self.cookie_d}"} if self.cookie_d else {}


def credentials_from_env(env: Mapping[str, str]) -> Credentials:
    token = env.get("SLACK_TOKEN", "").strip()
    cookie = env.get("SLACK_COOKIE_D", "").strip() or None
    if not token:
        raise ConfigError("Variable d'environnement SLACK_TOKEN absente.")
    if token.startswith("xoxc-"):
        if not cookie:
            raise ConfigError(
                "Un token xoxc- nécessite SLACK_COOKIE_D (cookie « d » de la session navigateur)."
            )
        return Credentials(token, cookie)
    if token.startswith("xoxp-"):
        return Credentials(token)
    raise ConfigError("SLACK_TOKEN doit être un token utilisateur (xoxp-… ou xoxc-…).")


def build_api(
    credentials: Credentials,
    *,
    call_delay: float = 1.2,
    notify: Callable[[str], None] = lambda message: None,
) -> SlackApi:
    retries = MAX_ATTEMPTS
    client = WebClient(
        token=credentials.token,
        headers=credentials.extra_headers(),
        retry_handlers=[
            NotifyingRateLimitHandler(notify, max_retry_count=retries),
            ConnectionErrorRetryHandler(max_retry_count=retries),
            ServerErrorRetryHandler(max_retry_count=retries),
        ],
    )
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {credentials.token}"
    if credentials.cookie_d:
        # Dans le cookie jar (et non en en-tête brut) pour survivre aux redirections.
        session.cookies.set("d", credentials.cookie_d, domain=".slack.com")
    return SlackApi(client, session, call_delay=call_delay, notify=notify)
