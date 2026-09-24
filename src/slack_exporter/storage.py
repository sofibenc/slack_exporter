"""Lecture et écriture de l'archive d'export sur disque."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

FORMAT_VERSION = 1

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_NAME_LENGTH = 100


def sanitize_filename(name: str) -> str:
    """Réduit un nom de fichier à [A-Za-z0-9._-], sans '..' ni point initial ou final.

    Au-delà de 100 caractères, le nom est tronqué en conservant son extension.
    """
    cleaned = _UNSAFE_CHARS.sub("_", name)
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.lstrip(".")
    if len(cleaned) > _MAX_NAME_LENGTH:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and 0 < len(suffix) <= 10:
            cleaned = stem[: _MAX_NAME_LENGTH - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:_MAX_NAME_LENGTH]
    # Windows et OneDrive refusent les noms qui finissent par un point.
    cleaned = cleaned.rstrip(".")
    return cleaned or "_"


def safe_join(base: Path, *parts: str) -> Path:
    """Joint des segments à `base` en refusant tout chemin qui en sortirait."""
    base_resolved = Path(base).resolve()
    candidate = base_resolved.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(f"chemin hors du dossier autorisé : {'/'.join(parts)}")
    return candidate


def _write_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class Archive:
    """Dossier d'archive : JSON de métadonnées, messages JSONL, fichiers joints."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write_json(self, name: str, data: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = safe_join(self.root, name)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def read_json(self, name: str, default: Any = None) -> Any:
        path = safe_join(self.root, name)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def conversation_dir(self, channel_id: str) -> Path:
        return safe_join(self.root, "conversations", channel_id)

    def reset_conversation(self, channel_id: str) -> None:
        """Efface messages et fils d'une conversation, en gardant ses fichiers."""
        conv_dir = self.conversation_dir(channel_id)
        (conv_dir / "messages.jsonl").unlink(missing_ok=True)
        shutil.rmtree(conv_dir / "replies", ignore_errors=True)

    def write_messages(self, channel_id: str, messages: Iterable[dict]) -> int:
        return _write_jsonl(self.conversation_dir(channel_id) / "messages.jsonl", messages)

    def read_messages(self, channel_id: str) -> list[dict]:
        return _read_jsonl(self.conversation_dir(channel_id) / "messages.jsonl")

    def write_replies(self, channel_id: str, thread_ts: str, replies: Iterable[dict]) -> int:
        path = safe_join(self.conversation_dir(channel_id), "replies", f"{thread_ts}.jsonl")
        return _write_jsonl(path, replies)

    def read_replies(self, channel_id: str, thread_ts: str) -> list[dict]:
        path = safe_join(self.conversation_dir(channel_id), "replies", f"{thread_ts}.jsonl")
        return _read_jsonl(path)

    def attachment_path(self, channel_id: str, file: dict) -> Path:
        """Emplacement d'un fichier joint Slack (dict de l'API) dans l'archive."""
        name = sanitize_filename(f"{file['id']}_{file.get('name') or file['id']}")
        return safe_join(self.conversation_dir(channel_id), "files", name)

    def load_state(self) -> dict:
        state = self.read_json("state.json", default=None) or {}
        return {
            "completed": list(state.get("completed", [])),
            "in_progress": state.get("in_progress"),
        }

    def save_state(self, state: dict) -> None:
        self.write_json("state.json", state)
