"""Orchestration de l'export : Slack -> archive sur disque, avec reprise."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from slack_exporter.slack_api import ApiError, AuthError, DownloadError
from slack_exporter.storage import FORMAT_VERSION, Archive

ALL_TYPES = ("public", "private", "im", "mpim")
_API_TYPES = {"public": "public_channel", "private": "private_channel", "im": "im", "mpim": "mpim"}
_SKIPPED_FILE_MODES = frozenset({"tombstone", "external", "hidden_by_limit"})


@dataclass
class ExportOptions:
    types: tuple[str, ...] = ALL_TYPES
    since: float | None = None  # timestamp Unix : n'exporter que les messages postérieurs
    only: tuple[str, ...] = ()  # noms ou identifiants de conversations
    refresh: bool = False


@dataclass
class ExportResult:
    conversations: int = 0
    messages: int = 0
    files: int = 0
    errors: list[dict] = field(default_factory=list)


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
) -> ExportResult:
    """Exporte les conversations sélectionnées. `api` expose l'interface de SlackApi."""
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

    state = {"completed": [], "in_progress": None} if options.refresh else archive.load_state()
    for conv in conversations:
        channel_id = conv["id"]
        label = conv["name"] or channel_id
        if channel_id in state["completed"]:
            # Les fichiers déjà présents sont sautés : seuls les échecs précédents sont retentés.
            stored = _stored_messages(archive, channel_id)
            _download_files(api, archive, channel_id, stored, options.refresh, result.errors)
            log(f"= {label} : déjà exporté")
            continue
        state["in_progress"] = channel_id
        archive.save_state(state)
        try:
            messages, files = _export_conversation(api, archive, channel_id, options, result.errors)
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
    api: Any, archive: Archive, channel_id: str, options: ExportOptions, errors: list[dict]
) -> tuple[int, int]:
    archive.reset_conversation(channel_id)
    oldest = f"{options.since:.6f}" if options.since is not None else None
    messages = sorted(api.iter_history(channel_id, oldest=oldest), key=lambda m: float(m["ts"]))
    archive.write_messages(channel_id, messages)

    with_files = list(messages)
    for message in messages:
        if message.get("reply_count", 0) > 0:
            thread_ts = message["ts"]
            replies = [r for r in api.iter_replies(channel_id, thread_ts) if r.get("ts") != thread_ts]
            archive.write_replies(channel_id, thread_ts, replies)
            with_files.extend(replies)

    files = _download_files(api, archive, channel_id, with_files, options.refresh, errors)
    return len(messages), files


def _download_files(
    api: Any,
    archive: Archive,
    channel_id: str,
    messages: list[dict],
    refresh: bool,
    errors: list[dict],
) -> int:
    available = 0
    for message in messages:
        for file in message.get("files", []):
            url = file.get("url_private_download") or file.get("url_private")
            if "id" not in file or not url or file.get("mode") in _SKIPPED_FILE_MODES:
                continue
            dest = archive.attachment_path(channel_id, file)
            if dest.exists() and not refresh:
                available += 1
                continue
            try:
                api.download(url, dest, file.get("mimetype"))
            except DownloadError as exc:
                errors.append({"channel": channel_id, "file": file["id"], "error": str(exc)})
                continue
            available += 1
    return available
