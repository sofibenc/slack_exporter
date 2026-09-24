"""Lecture des identifiants Slack et construction du client."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import requests
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
    ServerErrorRetryHandler,
)

from slack_exporter.slack_api import MAX_ATTEMPTS, SlackApi


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


def build_api(credentials: Credentials, *, call_delay: float = 1.2) -> SlackApi:
    retries = MAX_ATTEMPTS
    client = WebClient(
        token=credentials.token,
        headers=credentials.extra_headers(),
        retry_handlers=[
            RateLimitErrorRetryHandler(max_retry_count=retries),
            ConnectionErrorRetryHandler(max_retry_count=retries),
            ServerErrorRetryHandler(max_retry_count=retries),
        ],
    )
    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {credentials.token}", **credentials.extra_headers()}
    )
    return SlackApi(client, session, call_delay=call_delay)
