"""Orchestration de l'export : Slack -> archive sur disque, avec reprise et mise à jour."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from slack_exporter.formatting import conversation_title, user_display_names
from slack_exporter.slack_api import ApiError, AuthError, DownloadError
from slack_exporter.storage import FORMAT_VERSION, Archive

ALL_TYPES = ("public", "private", "im", "mpim")
_API_TYPES = {"public": "public_channel", "private": "private_channel", "im": "im", "mpim": "mpim"}
_SKIPPED_FILE_MODES = frozenset({"tombstone", "external", "hidden_by_limit"})
PROGRESS_EVERY = 200  # une ligne de statut par page de messages
UPDATE_WINDOW = 30 * 86400  # --update relit les 30 jours précédant le dernier message archivé


@dataclass
class ExportOptions:
    types: tuple[str, ...] = ALL_TYPES
    since: float | None = None  # timestamp Unix : n'exporter que les messages postérieurs
    only: tuple[str, ...] = ()  # noms ou identifiants de conversations
    refresh: bool = False
    update: bool = False  # relire la fenêtre récente des conversations déjà exportées


@dataclass
class ExportResult:
    conversations: int = 0
    messages: int = 0
    files: int = 0
    errors: list[dict] = field(default_factory=list)


class _Progress:
    """État courant d'une conversation, émis sous forme d'une ligne de statut."""

    def __init__(self, prefix: str, emit: Callable[[str], None]) -> None:
        self.prefix = prefix
        self._emit = emit
        self._last: str | None = None
        self.messages = 0
        self.threads_done = self.threads_total = 0
        self.files_done = self.files_total = 0
        self.current_file: str | None = None

    def update(self) -> None:
        parts = [f"messages : {self.messages}"]
        if self.threads_total:
            parts.append(f"fils : {self.threads_done}/{self.threads_total}")
        if self.files_total:
            files = f"fichiers : {self.files_done}/{self.files_total}"
            if self.current_file:
                files += f" ({self.current_file})"
            parts.append(files)
        status = f"⏳ {self.prefix} — " + " · ".join(parts)
        if status != self._last:
            self._last = status
            self._emit(status)


def conversation_type(conv: dict) -> str:
    if conv.get("is_im"):
        return "im"
    if conv.get("is_mpim"):
        return "mpim"
    if conv.get("is_private"):
        return "private"
    return "public"


def export(
    api: Any,
    archive: Archive,
    options: ExportOptions,
    log: Callable[[str], None] = print,
    progress: Callable[[str], None] = lambda status: None,
) -> ExportResult:
    """Exporte les conversations sélectionnées. `api` expose l'interface de SlackApi.

    `log` reçoit les lignes définitives, `progress` les lignes de statut temporaires.
    """
    identity = api.auth_test()
    log(f"Connecté à {identity.get('team')} en tant que {identity.get('user')}")
    result = ExportResult()
    _write_meta(archive, identity, result.errors)

    users = list(api.iter_users())
    archive.write_json("users.json", users)
    log(f"Utilisateurs : {len(users)}")
    conversations = _select_conversations(api, options)
    log(f"Conversations à exporter : {len(conversations)}")
    previous = archive.read_json("channels.json", [])
    archive.write_json("channels.json", _merge_conversations(previous, conversations))

    names = user_display_names(users)
    state = {"completed": [], "in_progress": None} if options.refresh else archive.load_state()
    for index, conv in enumerate(conversations, start=1):
        channel_id = conv["id"]
        label = f"[{index}/{len(conversations)}] {conversation_title(conv, names, identity.get('user_id'))}"
        tracker = _Progress(label, progress)
        if channel_id in state["completed"] and options.update:
            try:
                new, threads, files = _update_conversation(
                    api, archive, channel_id, result.errors, tracker
                )
            except AuthError:
                raise
            except ApiError as exc:
                result.errors.append({"channel": channel_id, "error": exc.code})
                log(f"! {label} : {exc.code}")
                continue
            result.conversations += 1
            result.messages += new
            result.files += files
            log(f"↻ {label} : +{new} messages, {threads} fils mis à jour, {files} fichiers")
            continue
        if channel_id in state["completed"]:
            # Les fichiers déjà présents sont sautés : seuls les échecs précédents sont retentés.
            tracker.messages = len(archive.read_messages(channel_id))
            stored = _stored_messages(archive, channel_id)
            _download_files(api, archive, channel_id, stored, options.refresh, result.errors, tracker)
            log(f"= {label} : déjà exporté")
            continue
        state["in_progress"] = channel_id
        archive.save_state(state)
        try:
            messages, files = _export_conversation(
                api, archive, channel_id, options, result.errors, tracker
            )
        except AuthError:
            raise
        except ApiError as exc:
            result.errors.append({"channel": channel_id, "error": exc.code})
            log(f"! {label} : {exc.code}")
            continue
        state["completed"].append(channel_id)
        state["in_progress"] = None
        archive.save_state(state)
        result.conversations += 1
        result.messages += messages
        result.files += files
        log(f"✓ {label} : {messages} messages, {files} fichiers")

    state["in_progress"] = None
    archive.save_state(state)
    _write_meta(archive, identity, result.errors)
    return result


def _write_meta(archive: Archive, identity: dict, errors: list[dict]) -> None:
    archive.write_json("meta.json", {
        "format_version": FORMAT_VERSION,
        "team": identity.get("team"),
        "team_id": identity.get("team_id"),
        "url": identity.get("url"),
        "user_id": identity.get("user_id"),
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "errors": errors,
    })


def _merge_conversations(previous: list[dict], selected: list[dict]) -> list[dict]:
    """Garde les conversations d'exports précédents, mises à jour par la sélection courante."""
    merged = {conv["id"]: conv for conv in previous}
    merged.update((conv["id"], conv) for conv in selected)
    return list(merged.values())


def _stored_messages(archive: Archive, channel_id: str) -> list[dict]:
    messages = archive.read_messages(channel_id)
    stored = list(messages)
    for message in messages:
        if message.get("reply_count", 0) > 0:
            stored.extend(archive.read_replies(channel_id, message["ts"]))
    return stored


def _select_conversations(api: Any, options: ExportOptions) -> list[dict]:
    selected = []
    for conv in api.iter_conversations([_API_TYPES[t] for t in options.types]):
        kind = conversation_type(conv)
        if kind not in options.types:
            continue
        # users.conversations ne renvoie que nos conversations ; on écarte seulement
        # celles explicitement marquées comme quittées.
        if conv.get("is_member") is False:
            continue
        if options.only and conv["id"] not in options.only and conv.get("name") not in options.only:
            continue
        entry = {"id": conv["id"], "name": conv.get("name") or "", "type": kind, "members": []}
        if kind == "im" and conv.get("user"):
            entry["members"] = [conv["user"]]
        elif kind == "mpim":
            try:
                entry["members"] = list(api.iter_members(conv["id"]))
            except AuthError:
                raise
            except ApiError:
                pass  # le rendu retombera sur le nom technique du groupe
        selected.append(entry)
    return selected


def _export_conversation(
    api: Any,
    archive: Archive,
    channel_id: str,
    options: ExportOptions,
    errors: list[dict],
    tracker: _Progress,
) -> tuple[int, int]:
    archive.reset_conversation(channel_id)
    oldest = f"{options.since:.6f}" if options.since is not None else None
    messages = _fetch_history(api, channel_id, oldest, tracker)
    archive.write_messages(channel_id, messages)
    threads = [m for m in messages if m.get("reply_count", 0) > 0]
    replies = _fetch_threads(api, archive, channel_id, threads, tracker)
    files = _download_files(api, archive, channel_id, messages + replies, options.refresh, errors, tracker)
    return len(messages), files


def _update_conversation(
    api: Any, archive: Archive, channel_id: str, errors: list[dict], tracker: _Progress
) -> tuple[int, int, int]:
    """Relit la fenêtre récente d'une conversation exportée et la fusionne à l'archive.

    Renvoie (nouveaux messages, fils relus, fichiers disponibles).
    """
    stored = archive.read_messages(channel_id)
    window_start = float(stored[-1]["ts"]) - UPDATE_WINDOW if stored else None
    oldest = f"{window_start:.6f}" if window_start is not None else None
    fetched = _fetch_history(api, channel_id, oldest, tracker)

    previous = {m["ts"]: m for m in stored}
    # Slack exclut `oldest` : un message pile à cette date n'est pas renvoyé, on le garde.
    kept = [m for m in stored if window_start is not None and float(m["ts"]) <= window_start]
    new = sum(1 for m in fetched if m["ts"] not in previous)
    threads = [
        m for m in fetched
        if m.get("reply_count", 0) > 0
        and (m["ts"] not in previous or previous[m["ts"]].get("latest_reply") != m.get("latest_reply"))
    ]
    _fetch_threads(api, archive, channel_id, threads, tracker)
    # Écrit en dernier : une interruption laisse l'historique précédent intact.
    archive.write_messages(channel_id, kept + fetched)

    stored_now = _stored_messages(archive, channel_id)
    files = _download_files(api, archive, channel_id, stored_now, False, errors, tracker)
    return new, len(threads), files


def _fetch_history(api: Any, channel_id: str, oldest: str | None, tracker: _Progress) -> list[dict]:
    tracker.update()
    fetched = []
    for message in api.iter_history(channel_id, oldest=oldest):
        fetched.append(message)
        if len(fetched) % PROGRESS_EVERY == 0:
            tracker.messages = len(fetched)
            tracker.update()
    tracker.messages = len(fetched)
    tracker.update()
    return sorted(fetched, key=lambda m: float(m["ts"]))


def _fetch_threads(
    api: Any, archive: Archive, channel_id: str, parents: list[dict], tracker: _Progress
) -> list[dict]:
    """Relit et enregistre les fils de `parents` ; renvoie toutes leurs réponses."""
    all_replies = []
    tracker.threads_total = len(parents)
    for message in parents:
        tracker.update()
        thread_ts = message["ts"]
        replies = [r for r in api.iter_replies(channel_id, thread_ts) if r.get("ts") != thread_ts]
        archive.write_replies(channel_id, thread_ts, replies)
        all_replies.extend(replies)
        tracker.threads_done += 1
    tracker.update()
    return all_replies


def _download_files(
    api: Any,
    archive: Archive,
    channel_id: str,
    messages: list[dict],
    refresh: bool,
    errors: list[dict],
    tracker: _Progress,
) -> int:
    candidates = [
        file
        for message in messages
        for file in message.get("files", [])
        if "id" in file
        and (file.get("url_private_download") or file.get("url_private"))
        and file.get("mode") not in _SKIPPED_FILE_MODES
    ]
    pending = []
    for file in candidates:
        dest = archive.attachment_path(channel_id, file)
        if dest.exists() and not refresh:
            tracker.files_done += 1
        else:
            pending.append((file, dest))
    tracker.files_total = len(candidates)
    available = tracker.files_done

    for file, dest in pending:
        tracker.current_file = file.get("name") or file["id"]
        tracker.update()
        try:
            api.download(file.get("url_private_download") or file["url_private"], dest, file.get("mimetype"))
        except DownloadError as exc:
            errors.append({"channel": channel_id, "file": file["id"], "error": str(exc)})
        else:
            available += 1
        tracker.files_done += 1
    tracker.current_file = None
    tracker.update()
    return available
