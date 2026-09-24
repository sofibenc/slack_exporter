"""Accès à l'API Slack : pagination, erreurs, limites de débit, téléchargements."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterator

import requests
from slack_sdk.errors import SlackApiError as _SdkApiError

FATAL_ERRORS = frozenset({"invalid_auth", "token_revoked", "not_authed", "account_inactive"})
MAX_ATTEMPTS = 5
PAGE_SIZE = 200


def rate_limit_notice(seconds: float) -> str:
    return f"Limite Slack atteinte, reprise dans {seconds:.0f} s…"


class ApiError(Exception):
    """Erreur renvoyée par Slack (`ok: false`), non fatale pour l'export."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthError(ApiError):
    """Token refusé : l'export doit s'arrêter."""


class DownloadError(Exception):
    """Échec définitif du téléchargement d'un fichier joint."""


class _RetryableDownload(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SlackApi:
    """Façade sur l'API Slack. `client` est un `slack_sdk.WebClient` (ou un double de test)."""

    def __init__(
        self,
        client: Any,
        session: requests.Session,
        *,
        call_delay: float = 1.2,
        sleep: Callable[[float], None] = time.sleep,
        notify: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.client = client
        self.session = session
        self._call_delay = call_delay
        self._sleep = sleep
        self._notify = notify

    def _call(self, method: str, **kwargs: Any) -> dict:
        try:
            response = getattr(self.client, method)(**kwargs)
        except _SdkApiError as exc:
            data = exc.response if exc.response is not None else {}
            code = data.get("error", "unknown_error")
            if code in FATAL_ERRORS:
                raise AuthError(code) from exc
            raise ApiError(code) from exc
        # slack_sdk renvoie un SlackResponse dont `.data` est le dict JSON.
        return getattr(response, "data", response)

    def _paginate(self, method: str, key: str, *, throttle: bool = False, **kwargs: Any) -> Iterator[dict]:
        cursor = None
        while True:
            if throttle and self._call_delay:
                self._sleep(self._call_delay)
            params = dict(kwargs, limit=PAGE_SIZE)
            if cursor:
                params["cursor"] = cursor
            response = self._call(method, **params)
            yield from response.get(key, [])
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    def auth_test(self) -> dict:
        return self._call("auth_test")

    def iter_users(self) -> Iterator[dict]:
        return self._paginate("users_list", "members")

    def iter_conversations(self, types: list[str]) -> Iterator[dict]:
        """Conversations dont l'utilisateur est membre (pas tous les canaux du workspace)."""
        return self._paginate(
            "users_conversations", "channels", types=",".join(types), exclude_archived=False
        )

    def iter_members(self, channel_id: str) -> Iterator[str]:
        return self._paginate("conversations_members", "members", channel=channel_id)

    def iter_history(self, channel_id: str, oldest: str | None = None) -> Iterator[dict]:
        kwargs: dict[str, Any] = {"channel": channel_id}
        if oldest is not None:
            kwargs["oldest"] = oldest
        return self._paginate("conversations_history", "messages", throttle=True, **kwargs)

    def iter_replies(self, channel_id: str, thread_ts: str) -> Iterator[dict]:
        return self._paginate(
            "conversations_replies", "messages", throttle=True, channel=channel_id, ts=thread_ts
        )

    def download(self, url: str, dest: Path, expected_mimetype: str | None = None) -> None:
        """Télécharge `url` vers `dest`, avec reprise sur 429, 5xx et coupures réseau."""
        last_error = "échec inconnu"
        for attempt in range(MAX_ATTEMPTS):
            try:
                self._download_once(url, dest, expected_mimetype)
                return
            except _RetryableDownload as exc:
                last_error = str(exc)
                if attempt < MAX_ATTEMPTS - 1:
                    if exc.retry_after is not None:
                        self._notify(rate_limit_notice(exc.retry_after))
                    self._sleep(exc.retry_after if exc.retry_after is not None else 2**attempt)
        raise DownloadError(last_error)

    def _download_once(self, url: str, dest: Path, expected_mimetype: str | None) -> None:
        try:
            response = self.session.get(url, stream=True, timeout=60)
        except requests.RequestException as exc:
            raise _RetryableDownload(f"erreur réseau : {exc}") from exc
        with response:
            status = response.status_code
            if status == 429:
                retry_after = float(response.headers.get("Retry-After", "5"))
                raise _RetryableDownload("HTTP 429", retry_after)
            if status >= 500:
                raise _RetryableDownload(f"HTTP {status}")
            if status != 200:
                raise DownloadError(f"HTTP {status}")
            content_type = response.headers.get("Content-Type", "")
            # Sans authentification valide, Slack répond 200 avec sa page de connexion.
            if content_type.startswith("text/html") and not (expected_mimetype or "").startswith("text/html"):
                raise DownloadError("réponse HTML inattendue (token ou cookie invalide ?)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_name(dest.name + ".part")
            try:
                with partial.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=65536):
                        fh.write(chunk)
            except requests.RequestException as exc:
                partial.unlink(missing_ok=True)
                raise _RetryableDownload(f"téléchargement interrompu : {exc}") from exc
            partial.replace(dest)
