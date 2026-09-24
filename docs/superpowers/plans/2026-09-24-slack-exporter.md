# Slack Exporter — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** un outil CLI Python qui exporte toutes les conversations Slack accessibles à l'utilisateur (pièces jointes comprises) puis génère une archive HTML statique consultable, à déposer dans Teams ou OneDrive.

**Architecture :** deux étapes indépendantes reliées par un format d'archive sur disque. `export` (slack_api → exporter → storage) interroge Slack avec reprise sur interruption ; `render` (storage → formatting → renderer) produit un site statique sans JavaScript. `slack_api` est le seul module qui parle à Slack ; `formatting` est pur.

**Tech Stack :** Python ≥ 3.11, slack_sdk, requests, Jinja2, click, pytest.

**Spec :** `docs/superpowers/specs/2026-09-24-slack-exporter-design.md`

## Global Constraints

- Python `>=3.11` ; dépendances runtime limitées à `slack_sdk>=3.27`, `jinja2>=3.1`, `click>=8.1`, `requests>=2.31` ; tests : `pytest>=8`.
- Layout `src/slack_exporter/`, point d'entrée `slack-exporter = "slack_exporter.cli:main"`.
- Environnement : `uv venv .venv && uv pip install -e '.[dev]'` ; tests via `.venv/bin/pytest`.
- Aucun test n'appelle le vrai Slack (doubles : `FakeClient`/`FakeSession` dans `tests/test_slack_api.py`, `FakeApi` dans `tests/fakes.py`).
- Tokens acceptés : `xoxp-…`, ou `xoxc-…` + `SLACK_COOKIE_D`. Le token n'est lu que depuis l'environnement et n'est jamais écrit dans l'archive ni dans les logs.
- Format d'archive `format_version` = `1` ; `render` refuse toute autre version.
- Noms de fichiers : `[A-Za-z0-9._-]` uniquement, 100 caractères max, sans `..` ; tout chemin est vérifié comme contenu dans son dossier cible.
- Liens autorisés dans le HTML : `http://`, `https://`, `mailto:` uniquement. Le site n'a aucun JavaScript ni ressource externe ; tous les liens sont relatifs.
- Pagination du rendu par année au-delà de `5000` messages racine.
- Retries : 5 tentatives au plus (API et téléchargements) ; `invalid_auth`, `token_revoked`, `not_authed`, `account_inactive` arrêtent l'export.
- Textes destinés à l'utilisateur (logs, erreurs, HTML) en français.

## Review Focus

1. **Téléchargement interrompu** (coupure réseau en plein fichier) : aucun fichier partiel ne doit être pris pour complet à la reprise. Écriture dans un `.part` puis renommage (Task 3, `test_interrupted_download_leaves_no_file_and_is_retried`).
2. **Token ou cookie expiré pour les fichiers** : Slack répond `200` avec sa page de connexion HTML. Ce n'est pas une pièce jointe valide et il faut une erreur (Task 3, `test_download_rejects_html_login_page`).
3. **Entités Slack dans le texte** (`&amp;`, `&lt;`, `&gt;`) : affichées une seule fois, sans double échappement et sans injection de HTML (Task 2, `test_slack_entities_are_not_double_escaped`, `test_raw_html_cannot_be_injected`).
4. **Messages sans `user`** (bots, webhooks, messages d'apps à `attachments` seuls) ou auteur supprimé ou inconnu : rendus avec un auteur lisible, sans plantage (Task 5, `test_messages_without_user_have_an_author`).
5. **Noms de fichiers hostiles ou en double** (`../x`, deux `image.png`) : pas d'écriture hors de l'archive ni de collision (Task 1, `test_attachment_path_is_sanitized_and_prefixed_with_file_id`, `test_same_name_files_do_not_collide`).

---

### Task 1 : Squelette du projet et stockage de l'archive

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/slack_exporter/__init__.py`
- Create: `src/slack_exporter/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes : rien.
- Produces (`slack_exporter.storage`) :
  - `FORMAT_VERSION: int = 1`
  - `sanitize_filename(name: str) -> str`
  - `safe_join(base: Path, *parts: str) -> Path` (lève `ValueError` si le chemin sort de `base`)
  - `class Archive(root: Path)` : `root`, `write_json(name, data)`, `read_json(name, default=None)`, `conversation_dir(channel_id) -> Path`, `reset_conversation(channel_id)`, `write_messages(channel_id, messages) -> int`, `read_messages(channel_id) -> list[dict]`, `write_replies(channel_id, thread_ts, replies) -> int`, `read_replies(channel_id, thread_ts) -> list[dict]`, `attachment_path(channel_id, file: dict) -> Path`, `load_state() -> {"completed": list[str], "in_progress": str | None}`, `save_state(state)`

- [ ] **Step 1 : Créer `pyproject.toml`**

````toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "slack-exporter"
version = "0.1.0"
description = "Exporte des conversations Slack vers une archive HTML consultable"
requires-python = ">=3.11"
dependencies = [
    "slack_sdk>=3.27",
    "jinja2>=3.1",
    "click>=8.1",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
slack-exporter = "slack_exporter.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
slack_exporter = ["templates/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
````

- [ ] **Step 2 : Créer `.gitignore`**

````text
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
archive/
site/
````

- [ ] **Step 3 : Créer `src/slack_exporter/__init__.py`**

````python
"""Exporteur Slack vers une archive HTML consultable."""
````

- [ ] **Step 4 : Installer l'environnement**

```bash
mkdir -p src/slack_exporter/templates tests
uv venv .venv && uv pip install -e '.[dev]'
```
Expected : installation sans erreur (le module `cli` n'existe pas encore, c'est normal).

- [ ] **Step 5 : Écrire les tests (qui échouent) — `tests/test_storage.py`**

````python
import pytest

from slack_exporter.storage import Archive, safe_join, sanitize_filename


def test_sanitize_filename_keeps_safe_characters():
    assert sanitize_filename("rapport-2024_v1.pdf") == "rapport-2024_v1.pdf"


def test_sanitize_filename_replaces_unsafe_characters():
    assert sanitize_filename("mon fichier (1)/é.png") == "mon_fichier__1___.png"


def test_sanitize_filename_removes_traversal_and_leading_dots():
    assert sanitize_filename("../../etc/passwd") == "____etc_passwd"
    assert sanitize_filename(".bashrc") == "bashrc"
    assert sanitize_filename("..") == "_"
    assert sanitize_filename("") == "_"


def test_sanitize_filename_truncates_to_100_characters():
    assert len(sanitize_filename("a" * 300)) == 100


def test_safe_join_accepts_inner_path(tmp_path):
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_safe_join_rejects_escape(tmp_path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "dehors")


def test_json_round_trip(tmp_path):
    archive = Archive(tmp_path / "archive")
    archive.write_json("users.json", [{"id": "U1", "name": "élodie"}])
    assert archive.read_json("users.json") == [{"id": "U1", "name": "élodie"}]
    assert archive.read_json("absent.json", default=[]) == []


def test_messages_round_trip_preserves_order(tmp_path):
    archive = Archive(tmp_path)
    messages = [{"ts": "1.0", "text": "a"}, {"ts": "2.0", "text": "b"}]
    assert archive.write_messages("C1", messages) == 2
    assert archive.read_messages("C1") == messages
    assert archive.read_messages("C_ABSENT") == []


def test_replies_round_trip(tmp_path):
    archive = Archive(tmp_path)
    archive.write_replies("C1", "1.0", [{"ts": "1.5", "text": "réponse"}])
    assert archive.read_replies("C1", "1.0") == [{"ts": "1.5", "text": "réponse"}]
    assert archive.read_replies("C1", "9.0") == []


def test_channel_id_cannot_escape_archive(tmp_path):
    archive = Archive(tmp_path)
    with pytest.raises(ValueError):
        archive.write_messages("../../dehors", [])


def test_attachment_path_is_sanitized_and_prefixed_with_file_id(tmp_path):
    archive = Archive(tmp_path)
    path = archive.attachment_path("C1", {"id": "F1", "name": "../photo de vacances.jpg"})
    assert path == (tmp_path / "conversations" / "C1" / "files" / "F1___photo_de_vacances.jpg").resolve()
    assert archive.attachment_path("C1", {"id": "F2"}).name == "F2_F2"


def test_same_name_files_do_not_collide(tmp_path):
    archive = Archive(tmp_path)
    a = archive.attachment_path("C1", {"id": "F1", "name": "image.png"})
    b = archive.attachment_path("C1", {"id": "F2", "name": "image.png"})
    assert a != b


def test_reset_conversation_keeps_files(tmp_path):
    archive = Archive(tmp_path)
    archive.write_messages("C1", [{"ts": "1.0"}])
    archive.write_replies("C1", "1.0", [{"ts": "1.1"}])
    kept = archive.attachment_path("C1", {"id": "F1", "name": "a.txt"})
    kept.parent.mkdir(parents=True)
    kept.write_text("x")

    archive.reset_conversation("C1")

    assert archive.read_messages("C1") == []
    assert archive.read_replies("C1", "1.0") == []
    assert kept.exists()


def test_state_defaults_and_round_trip(tmp_path):
    archive = Archive(tmp_path)
    assert archive.load_state() == {"completed": [], "in_progress": None}
    archive.save_state({"completed": ["C1"], "in_progress": "C2"})
    assert archive.load_state() == {"completed": ["C1"], "in_progress": "C2"}
````

- [ ] **Step 6 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_storage.py -q`
Expected : FAIL (ModuleNotFoundError: No module named 'slack_exporter.storage')

- [ ] **Step 7 : Implémenter `src/slack_exporter/storage.py`**

````python
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
    """Réduit un nom de fichier à [A-Za-z0-9._-], sans '..' ni point initial."""
    cleaned = _UNSAFE_CHARS.sub("_", name)
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.lstrip(".")[:_MAX_NAME_LENGTH]
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
````

- [ ] **Step 8 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 9 : Commit**

```bash
git add pyproject.toml .gitignore src/slack_exporter/__init__.py src/slack_exporter/storage.py tests/test_storage.py
git commit -m "feat: add project skeleton and archive storage" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2 : Conversion mrkdwn Slack → HTML

**Files:**
- Create: `src/slack_exporter/emoji_table.py`
- Create: `src/slack_exporter/formatting.py`
- Test: `tests/test_formatting.py`

**Interfaces:**
- Consumes : rien (module pur).
- Produces (`slack_exporter.formatting`) :
  - `@dataclass FormatContext(users: dict[str, str] = {}, channels: dict[str, str] = {})` : `users` = id → nom affiché ; `channels` = id → nom **des conversations exportées uniquement** (sa présence détermine si `<#C…>` devient un lien `../<id>/index.html`)
  - `mrkdwn_to_html(text: str, ctx: FormatContext) -> str` : fragment HTML sûr, à marquer `Markup` par l'appelant
  - `emoji_to_unicode(name: str) -> str | None` (ignore le suffixe `::skin-tone-N`)
- `slack_exporter.emoji_table.EMOJI: dict[str, str]`

- [ ] **Step 1 : Écrire les tests (qui échouent) — `tests/test_formatting.py`**

````python
from slack_exporter.formatting import FormatContext, emoji_to_unicode, mrkdwn_to_html

CTX = FormatContext(users={"U1": "Alice"}, channels={"C1": "general"})


def fmt(text: str) -> str:
    return mrkdwn_to_html(text, CTX)


def test_plain_text_unchanged():
    assert fmt("bonjour") == "bonjour"


def test_slack_entities_are_not_double_escaped():
    # Slack envoie « a & b <tag> » sous la forme « a &amp; b &lt;tag&gt; ».
    assert fmt("a &amp; b &lt;tag&gt;") == "a &amp; b &lt;tag&gt;"


def test_literal_entity_typed_by_user_is_preserved():
    # L'utilisateur a tapé « &lt; » : Slack l'envoie « &amp;lt; ».
    assert fmt("&amp;lt;") == "&amp;lt;"


def test_raw_html_cannot_be_injected():
    out = fmt("<script>alert(1)</script> &lt;img src=x onerror=alert(1)&gt;")
    assert "<script" not in out
    assert "<img" not in out


def test_newlines_become_br():
    assert fmt("a\nb") == "a<br>b"


def test_bold_italic_strike():
    assert fmt("*gras* _ital_ ~barré~") == "<strong>gras</strong> <em>ital</em> <del>barré</del>"


def test_underscores_inside_words_are_not_italic():
    assert fmt("mon_nom_de_variable") == "mon_nom_de_variable"


def test_inline_code_is_not_formatted():
    assert fmt("`*pas gras*`") == "<code>*pas gras*</code>"


def test_code_block_keeps_newlines_and_unescapes_entities():
    assert fmt("```\nx = 1 &lt; 2\ny = *z*\n```") == "<pre><code>x = 1 &lt; 2\ny = *z*</code></pre>"


def test_blockquote():
    assert fmt("&gt; cité\n&gt; suite\nnormal") == "<blockquote>cité<br>suite</blockquote>normal"


def test_known_user_mention():
    assert fmt("<@U1> salut") == '<span class="mention">@Alice</span> salut'


def test_unknown_user_mention_falls_back_to_label_then_id():
    assert fmt("<@U9|bob>") == '<span class="mention">@bob</span>'
    assert fmt("<@U9>") == '<span class="mention">@U9</span>'


def test_exported_channel_is_linked():
    assert fmt("<#C1|general>") == '<a class="channel" href="../C1/index.html">#general</a>'


def test_non_exported_channel_is_plain_text():
    assert fmt("<#C2|secret>") == '<span class="channel">#secret</span>'


def test_special_mentions():
    assert fmt("<!here>") == '<span class="mention">@here</span>'
    assert fmt("<!channel>") == '<span class="mention">@channel</span>'
    assert fmt("<!subteam^S1|@devs>") == '<span class="mention">@devs</span>'


def test_date_token_uses_fallback_label():
    assert fmt("<!date^1392734382^{date}|13 fév. 2014>") == "13 fév. 2014"


def test_link_with_label_and_escaped_ampersand():
    assert fmt("<https://ex.com/a?b=1&amp;c=2|site>") == (
        '<a href="https://ex.com/a?b=1&amp;c=2" rel="noopener noreferrer">site</a>'
    )


def test_bare_link():
    assert fmt("<https://ex.com>") == '<a href="https://ex.com" rel="noopener noreferrer">https://ex.com</a>'


def test_mailto_link():
    assert fmt("<mailto:a@b.fr|a@b.fr>") == '<a href="mailto:a@b.fr" rel="noopener noreferrer">a@b.fr</a>'


def test_dangerous_scheme_is_not_linked():
    out = fmt("<javascript:alert(1)|clic>")
    assert out == "clic"


def test_link_label_is_escaped():
    assert fmt("<https://ex.com|a &lt;b&gt;>") == '<a href="https://ex.com" rel="noopener noreferrer">a &lt;b&gt;</a>'


def test_quote_in_url_cannot_break_out_of_attribute():
    out = fmt('<https://ex.com/" onmouseover="x|t>')
    assert out == '<a href="https://ex.com/&quot; onmouseover=&quot;x" rel="noopener noreferrer">t</a>'


def test_bold_around_mention():
    assert fmt("*<@U1>*") == '<strong><span class="mention">@Alice</span></strong>'


def test_emoji():
    assert fmt(":smile: :inconnu:") == "😄 :inconnu:"


def test_time_is_not_taken_for_emoji():
    assert fmt("rdv à 10:30:45") == "rdv à 10:30:45"


def test_emoji_to_unicode_ignores_skin_tone():
    assert emoji_to_unicode("+1::skin-tone-3") == "👍"
    assert emoji_to_unicode("perso") is None
````

- [ ] **Step 2 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_formatting.py -q`
Expected : FAIL (ModuleNotFoundError: No module named 'slack_exporter.formatting')

- [ ] **Step 3 : Implémenter `src/slack_exporter/emoji_table.py`**

````python
"""Correspondance des codes emoji Slack courants vers Unicode."""

EMOJI: dict[str, str] = {
    "smile": "😄", "smiley": "😃", "grinning": "😀", "grin": "😁", "laughing": "😆",
    "sweat_smile": "😅", "joy": "😂", "rolling_on_the_floor_laughing": "🤣",
    "slightly_smiling_face": "🙂", "upside_down_face": "🙃", "wink": "😉", "blush": "😊",
    "innocent": "😇", "heart_eyes": "😍", "kissing_heart": "😘", "yum": "😋",
    "stuck_out_tongue": "😛", "stuck_out_tongue_winking_eye": "😜", "thinking_face": "🤔",
    "neutral_face": "😐", "expressionless": "😑", "no_mouth": "😶", "smirk": "😏",
    "unamused": "😒", "face_with_rolling_eyes": "🙄", "grimacing": "😬", "relieved": "😌",
    "pensive": "😔", "sleepy": "😪", "sleeping": "😴", "mask": "😷", "nerd_face": "🤓",
    "sunglasses": "😎", "confused": "😕", "worried": "😟", "slightly_frowning_face": "🙁",
    "open_mouth": "😮", "astonished": "😲", "flushed": "😳", "pleading_face": "🥺",
    "cry": "😢", "sob": "😭", "scream": "😱", "confounded": "😖", "disappointed": "😞",
    "sweat": "😓", "weary": "😩", "tired_face": "😫", "triumph": "😤", "rage": "😡",
    "angry": "😠", "skull": "💀", "poop": "💩", "hankey": "💩", "clown_face": "🤡",
    "ghost": "👻", "alien": "👽", "robot_face": "🤖", "see_no_evil": "🙈",
    "hugging_face": "🤗", "face_palm": "🤦", "facepalm": "🤦", "shrug": "🤷",
    "partying_face": "🥳", "star-struck": "🤩", "zany_face": "🤪", "exploding_head": "🤯",
    "+1": "👍", "thumbsup": "👍", "-1": "👎", "thumbsdown": "👎", "ok_hand": "👌",
    "wave": "👋", "clap": "👏", "raised_hands": "🙌", "pray": "🙏", "muscle": "💪",
    "point_up": "☝️", "point_right": "👉", "point_left": "👈", "v": "✌️",
    "crossed_fingers": "🤞", "handshake": "🤝", "eyes": "👀", "brain": "🧠",
    "heart": "❤️", "orange_heart": "🧡", "yellow_heart": "💛", "green_heart": "💚",
    "blue_heart": "💙", "purple_heart": "💜", "black_heart": "🖤", "broken_heart": "💔",
    "sparkling_heart": "💖", "100": "💯", "fire": "🔥", "star": "⭐", "sparkles": "✨",
    "boom": "💥", "zap": "⚡", "tada": "🎉", "confetti_ball": "🎊", "balloon": "🎈",
    "gift": "🎁", "trophy": "🏆", "medal": "🏅", "rocket": "🚀", "bulb": "💡",
    "warning": "⚠️", "no_entry": "⛔", "x": "❌", "white_check_mark": "✅",
    "heavy_check_mark": "✔️", "ballot_box_with_check": "☑️", "question": "❓",
    "exclamation": "❗", "bangbang": "‼️", "red_circle": "🔴", "large_green_circle": "🟢",
    "large_blue_circle": "🔵", "arrow_right": "➡️", "arrow_left": "⬅️",
    "arrow_up": "⬆️", "arrow_down": "⬇️", "link": "🔗", "lock": "🔒", "key": "🔑",
    "memo": "📝", "pencil": "📝", "pencil2": "✏️", "calendar": "📆", "date": "📅",
    "clock": "🕐", "hourglass": "⌛", "email": "📧", "envelope": "✉️", "phone": "📞",
    "computer": "💻", "bug": "🐛", "wrench": "🔧", "hammer": "🔨", "gear": "⚙️",
    "chart_with_upwards_trend": "📈", "chart_with_downwards_trend": "📉",
    "bar_chart": "📊", "clipboard": "📋", "pushpin": "📌", "paperclip": "📎",
    "mag": "🔍", "books": "📚", "moneybag": "💰", "coffee": "☕", "beer": "🍺",
    "beers": "🍻", "pizza": "🍕", "cake": "🍰", "birthday": "🎂", "sunny": "☀️",
    "cloud": "☁️", "umbrella": "☂️", "snowflake": "❄️", "rainbow": "🌈",
    "earth_africa": "🌍", "house": "🏠", "car": "🚗", "airplane": "✈️",
    "dog": "🐶", "cat": "🐱", "unicorn_face": "🦄", "see_no_evil_monkey": "🙈",
    "speech_balloon": "💬", "thought_balloon": "💭", "zzz": "💤", "wavy_dash": "〰️",
    "heavy_plus_sign": "➕", "heavy_minus_sign": "➖", "sos": "🆘", "new": "🆕",
    "free": "🆓", "ok": "🆗", "cool": "🆒", "up": "🆙",
}
````

- [ ] **Step 4 : Implémenter `src/slack_exporter/formatting.py`**

````python
"""Conversion du mrkdwn Slack en HTML sûr (fonctions pures, sans I/O)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from slack_exporter.emoji_table import EMOJI

ALLOWED_SCHEMES = ("http://", "https://", "mailto:")

_CODE_BLOCK = re.compile(r"```(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_CONTROL = re.compile(r"<([^<>\n]+)>")
_BOLD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_ITALIC = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])")
_EMOJI = re.compile(r":([a-z0-9_+-]+(?:::skin-tone-[2-6])?):")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")


@dataclass
class FormatContext:
    """Ce qu'il faut pour résoudre les références Slack dans un message."""

    users: dict[str, str] = field(default_factory=dict)  # id utilisateur -> nom affiché
    channels: dict[str, str] = field(default_factory=dict)  # id conversation exportée -> nom


def emoji_to_unicode(name: str) -> str | None:
    """Renvoie l'emoji Unicode d'un code Slack (`smile`, `+1::skin-tone-2`), ou None."""
    return EMOJI.get(name.split("::", 1)[0])


def _unescape_slack(text: str) -> str:
    # Slack n'encode que ces trois entités dans le champ `text`.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _render_control(inner: str, ctx: FormatContext) -> str:
    target, _, label = inner.partition("|")
    target = _unescape_slack(target)
    label = _unescape_slack(label)
    if target.startswith("@"):
        user_id = target[1:]
        name = ctx.users.get(user_id) or label or user_id
        return f'<span class="mention">@{_esc(name)}</span>'
    if target.startswith("#"):
        channel_id = target[1:]
        name = ctx.channels.get(channel_id) or label or channel_id
        if channel_id in ctx.channels:
            return f'<a class="channel" href="../{_esc(channel_id)}/index.html">#{_esc(name)}</a>'
        return f'<span class="channel">#{_esc(name)}</span>'
    if target.startswith("!"):
        command = target[1:]
        if command in ("here", "channel", "everyone"):
            return f'<span class="mention">@{command}</span>'
        if command.startswith("subteam^"):
            group = label or "@" + command.split("^", 1)[1]
            return f'<span class="mention">{_esc(group)}</span>'
        return _esc(label or command)
    if target.lower().startswith(ALLOWED_SCHEMES):
        return f'<a href="{_esc(target)}" rel="noopener noreferrer">{_esc(label or target)}</a>'
    return _esc(label or target)


def _replace_emoji(match: re.Match[str]) -> str:
    return emoji_to_unicode(match.group(1)) or match.group(0)


def _blockquotes(text: str) -> str:
    out: list[str] = []
    quote: list[str] = []
    for line in text.split("\n"):
        if line.startswith("&gt;"):
            quote.append(line[len("&gt;"):].removeprefix(" "))
            continue
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote = []
        out.append(line)
    if quote:
        out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    return "\n".join(out)


def mrkdwn_to_html(text: str, ctx: FormatContext) -> str:
    """Convertit le champ `text` d'un message Slack en fragment HTML sûr."""
    fragments: list[str] = []

    def stash(fragment: str) -> str:
        fragments.append(fragment)
        return f"\x00{len(fragments) - 1}\x00"

    text = text.replace("\x00", "")
    text = _CODE_BLOCK.sub(
        lambda m: stash(f"<pre><code>{_esc(_unescape_slack(m.group(1).strip(chr(10))))}</code></pre>"),
        text,
    )
    text = _INLINE_CODE.sub(lambda m: stash(f"<code>{_esc(_unescape_slack(m.group(1)))}</code>"), text)
    text = _CONTROL.sub(lambda m: stash(_render_control(m.group(1), ctx)), text)

    # À partir d'ici, le texte restant est échappé : on n'y ajoute que du balisage contrôlé.
    text = _esc(_unescape_slack(text))
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _STRIKE.sub(r"<del>\1</del>", text)
    text = _EMOJI.sub(_replace_emoji, text)
    text = _blockquotes(text)
    text = text.replace("\n", "<br>").replace("</blockquote><br>", "</blockquote>")
    return _PLACEHOLDER.sub(lambda m: fragments[int(m.group(1))], text)
````

- [ ] **Step 5 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 6 : Commit**

```bash
git add src/slack_exporter/emoji_table.py src/slack_exporter/formatting.py tests/test_formatting.py
git commit -m "feat: convert Slack mrkdwn to safe HTML" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3 : Client API Slack et authentification

**Files:**
- Create: `src/slack_exporter/slack_api.py`
- Create: `src/slack_exporter/auth.py`
- Test: `tests/test_slack_api.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes : rien des tâches précédentes.
- Produces (`slack_exporter.slack_api`) :
  - `FATAL_ERRORS`, `MAX_ATTEMPTS = 5`, `PAGE_SIZE = 200`
  - `class ApiError(Exception)` avec `.code: str` ; `class AuthError(ApiError)` ; `class DownloadError(Exception)`
  - `class SlackApi(client, session, *, call_delay: float = 1.2, sleep=time.sleep)` avec attributs publics `client`, `session` et méthodes : `auth_test() -> dict`, `iter_users() -> Iterator[dict]`, `iter_conversations(types: list[str]) -> Iterator[dict]` (types API : `public_channel`, `private_channel`, `im`, `mpim`), `iter_members(channel_id) -> Iterator[str]`, `iter_history(channel_id, oldest: str | None = None) -> Iterator[dict]` (ordre API : récent d'abord), `iter_replies(channel_id, thread_ts) -> Iterator[dict]` (parent inclus), `download(url, dest: Path, expected_mimetype: str | None = None) -> None`
- Produces (`slack_exporter.auth`) : `class ConfigError(Exception)`, `@dataclass(frozen=True) Credentials(token, cookie_d=None)` avec `extra_headers() -> dict[str, str]`, `credentials_from_env(env: Mapping[str, str]) -> Credentials`, `build_api(credentials, *, call_delay=1.2) -> SlackApi`

- [ ] **Step 1 : Écrire les tests (qui échouent) — `tests/test_slack_api.py`**

````python
import pytest
import requests
from slack_sdk.errors import SlackApiError

from slack_exporter.slack_api import ApiError, AuthError, DownloadError, SlackApi


class FakeClient:
    """Double de slack_sdk.WebClient : chaque méthode dépile ses réponses."""

    def __init__(self, responses):
        self.responses = {method: list(items) for method, items in responses.items()}
        self.calls = []

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            response = self.responses[method].pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return call


class FakeSlackResponse:
    def __init__(self, data):
        self.data = data


class FakeResponse:
    def __init__(self, status=200, body=b"donnees", headers=None, fail_midway=False):
        self.status_code = status
        self.body = body
        self.headers = headers if headers is not None else {"Content-Type": "application/pdf"}
        self.fail_midway = fail_midway

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        yield self.body[:2]
        if self.fail_midway:
            raise requests.ConnectionError("coupure")
        yield self.body[2:]


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_api(client=None, session=None, sleeps=None, call_delay=0.0):
    sleeps = sleeps if sleeps is not None else []
    return SlackApi(
        client or FakeClient({}),
        session or FakeSession([]),
        call_delay=call_delay,
        sleep=sleeps.append,
    )


def slack_error(code):
    return SlackApiError("erreur", {"ok": False, "error": code})


def test_paginates_with_cursor():
    client = FakeClient({
        "users_list": [
            {"members": [{"id": "U1"}], "response_metadata": {"next_cursor": "c2"}},
            {"members": [{"id": "U2"}], "response_metadata": {"next_cursor": ""}},
        ]
    })
    api = make_api(client)

    assert [u["id"] for u in api.iter_users()] == ["U1", "U2"]
    assert client.calls == [
        ("users_list", {"limit": 200}),
        ("users_list", {"limit": 200, "cursor": "c2"}),
    ]


def test_conversations_list_requests_types_and_archived():
    client = FakeClient({"conversations_list": [{"channels": [{"id": "C1"}]}]})
    api = make_api(client)

    assert list(api.iter_conversations(["public_channel", "im"])) == [{"id": "C1"}]
    assert client.calls[0][1] == {
        "types": "public_channel,im", "exclude_archived": False, "limit": 200,
    }


def test_history_passes_oldest_and_throttles():
    client = FakeClient({"conversations_history": [{"messages": [{"ts": "1.0"}]}]})
    sleeps = []
    api = make_api(client, sleeps=sleeps, call_delay=1.5)

    assert list(api.iter_history("C1", oldest="100.000000")) == [{"ts": "1.0"}]
    assert client.calls[0][1] == {"channel": "C1", "oldest": "100.000000", "limit": 200}
    assert sleeps == [1.5]


def test_replies_request_thread():
    client = FakeClient({"conversations_replies": [{"messages": [{"ts": "1.0"}, {"ts": "1.1"}]}]})
    api = make_api(client)

    assert len(list(api.iter_replies("C1", "1.0"))) == 2
    assert client.calls[0][1] == {"channel": "C1", "ts": "1.0", "limit": 200}


def test_auth_test_unwraps_slack_response():
    client = FakeClient({"auth_test": [FakeSlackResponse({"ok": True, "user_id": "U1"})]})
    assert make_api(client).auth_test() == {"ok": True, "user_id": "U1"}


def test_fatal_auth_errors_raise_auth_error():
    client = FakeClient({"auth_test": [slack_error("invalid_auth")]})
    with pytest.raises(AuthError) as info:
        make_api(client).auth_test()
    assert info.value.code == "invalid_auth"


def test_other_errors_raise_api_error_with_code():
    client = FakeClient({"conversations_history": [slack_error("not_in_channel")]})
    with pytest.raises(ApiError) as info:
        list(make_api(client).iter_history("C1"))
    assert info.value.code == "not_in_channel"
    assert not isinstance(info.value, AuthError)


def test_download_writes_file_without_leftover_partial(tmp_path):
    dest = tmp_path / "files" / "F1_a.pdf"
    api = make_api(session=FakeSession([FakeResponse(body=b"contenu")]))

    api.download("https://files/F1", dest, "application/pdf")

    assert dest.read_bytes() == b"contenu"
    assert list(dest.parent.iterdir()) == [dest]


def test_download_waits_retry_after_on_429(tmp_path):
    sleeps = []
    session = FakeSession([FakeResponse(status=429, headers={"Retry-After": "3"}), FakeResponse()])
    api = make_api(session=session, sleeps=sleeps)

    api.download("https://files/F1", tmp_path / "f")

    assert sleeps == [3.0]
    assert len(session.calls) == 2


def test_download_retries_server_errors_and_network_errors(tmp_path):
    sleeps = []
    session = FakeSession([
        FakeResponse(status=502),
        requests.ConnectionError("réseau"),
        FakeResponse(body=b"ok!"),
    ])
    api = make_api(session=session, sleeps=sleeps)

    api.download("https://files/F1", tmp_path / "f")

    assert (tmp_path / "f").read_bytes() == b"ok!"
    assert sleeps == [1, 2]


def test_download_client_error_is_not_retried(tmp_path):
    session = FakeSession([FakeResponse(status=404)])
    api = make_api(session=session)

    with pytest.raises(DownloadError, match="404"):
        api.download("https://files/F1", tmp_path / "f")
    assert len(session.calls) == 1
    assert not (tmp_path / "f").exists()


def test_download_rejects_html_login_page(tmp_path):
    session = FakeSession([FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"})])
    api = make_api(session=session)

    with pytest.raises(DownloadError, match="HTML"):
        api.download("https://files/F1", tmp_path / "f", "image/png")
    assert not (tmp_path / "f").exists()


def test_download_accepts_html_file_when_expected(tmp_path):
    session = FakeSession([FakeResponse(headers={"Content-Type": "text/html"}, body=b"<p>hi</p>")])
    make_api(session=session).download("https://files/F1", tmp_path / "f.html", "text/html")
    assert (tmp_path / "f.html").read_bytes() == b"<p>hi</p>"


def test_interrupted_download_leaves_no_file_and_is_retried(tmp_path):
    dest = tmp_path / "f"
    session = FakeSession([FakeResponse(fail_midway=True), FakeResponse(body=b"complet")])
    api = make_api(session=session)

    api.download("https://files/F1", dest)

    assert dest.read_bytes() == b"complet"
    assert not (tmp_path / "f.part").exists()


def test_download_gives_up_after_five_attempts(tmp_path):
    session = FakeSession([FakeResponse(status=503) for _ in range(5)])
    sleeps = []
    api = make_api(session=session, sleeps=sleeps)

    with pytest.raises(DownloadError, match="503"):
        api.download("https://files/F1", tmp_path / "f")
    assert len(session.calls) == 5
    assert len(sleeps) == 4
    assert list(tmp_path.iterdir()) == []
````

- [ ] **Step 2 : Écrire les tests (qui échouent) — `tests/test_auth.py`**

````python
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
````

- [ ] **Step 3 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_slack_api.py tests/test_auth.py -q`
Expected : FAIL (ModuleNotFoundError: No module named 'slack_exporter.slack_api')

- [ ] **Step 4 : Implémenter `src/slack_exporter/slack_api.py`**

````python
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
    ) -> None:
        self.client = client
        self.session = session
        self._call_delay = call_delay
        self._sleep = sleep

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
        return self._paginate(
            "conversations_list", "channels", types=",".join(types), exclude_archived=False
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
````

- [ ] **Step 5 : Implémenter `src/slack_exporter/auth.py`**

````python
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
````

- [ ] **Step 6 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 7 : Commit**

```bash
git add src/slack_exporter/slack_api.py src/slack_exporter/auth.py tests/test_slack_api.py tests/test_auth.py
git commit -m "feat: add Slack API client with pagination, retries and downloads" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4 : Orchestration de l'export avec reprise

**Files:**
- Create: `src/slack_exporter/exporter.py`
- Create: `tests/fakes.py`
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes : `Archive`, `FORMAT_VERSION` (Task 1) ; `ApiError`, `AuthError`, `DownloadError` et l'interface de `SlackApi` (Task 3).
- Produces (`slack_exporter.exporter`) :
  - `ALL_TYPES = ("public", "private", "im", "mpim")`
  - `@dataclass ExportOptions(types=ALL_TYPES, since: float | None = None, only: tuple[str, ...] = (), refresh: bool = False)` (`since` = timestamp Unix)
  - `@dataclass ExportResult(conversations=0, messages=0, files=0, errors: list[dict] = [])`
  - `conversation_type(conv: dict) -> str`
  - `export(api, archive: Archive, options: ExportOptions, log=print) -> ExportResult`
  - Contrat de `channels.json` : liste de `{"id", "name", "type", "members"}` (`members` = `[autre utilisateur]` pour `im`, membres pour `mpim`, `[]` sinon)
  - Contrat de `meta.json` : `{"format_version", "team", "team_id", "url", "user_id", "exported_at", "errors"}`, écrit au début puis à la fin de l'export
  - Entrée d'erreur : `{"channel": id, "error": code}` ou `{"channel": id, "file": file_id, "error": message}`
- Produces (`tests/fakes.py`, importé par `from fakes import ...`) : `FakeApi`, `msg(ts, text="", user="U1", **extra)`, `slack_file(file_id, name="doc.pdf", mimetype="application/pdf", **extra)`

- [ ] **Step 1 : Écrire les tests (qui échouent) — `tests/fakes.py`**

````python
"""Doubles de test partagés : une fausse SlackApi entièrement en mémoire."""
from slack_exporter.slack_api import DownloadError


def msg(ts, text="", user="U1", **extra):
    return {"type": "message", "ts": ts, "text": text, "user": user, **extra}


def slack_file(file_id, name="doc.pdf", mimetype="application/pdf", **extra):
    return {
        "id": file_id,
        "name": name,
        "mimetype": mimetype,
        "size": 2048,
        "url_private_download": f"https://files.slack.com/{file_id}/{name}",
        **extra,
    }


def _api_type(conv):
    if conv.get("is_im"):
        return "im"
    if conv.get("is_mpim"):
        return "mpim"
    return "private_channel" if conv.get("is_private") else "public_channel"


class FakeApi:
    """Reproduit l'interface de SlackApi. `history` est dans l'ordre de l'API (récent d'abord)."""

    def __init__(self, *, users=(), conversations=(), history=None, replies=None,
                 members=None, failing_channels=None, failing_files=(), identity=None):
        self.identity = identity or {
            "ok": True, "team": "Acme", "team_id": "T1",
            "url": "https://acme.slack.com/", "user_id": "U1",
        }
        self.users = list(users)
        self.conversations = list(conversations)
        self.history = history or {}
        self.replies = replies or {}
        self.members = members or {}
        self.failing_channels = failing_channels or {}
        self.failing_files = set(failing_files)
        self.calls = []

    def auth_test(self):
        self.calls.append(("auth_test",))
        return self.identity

    def iter_users(self):
        return iter(self.users)

    def iter_conversations(self, types):
        self.calls.append(("conversations", tuple(types)))
        return iter([c for c in self.conversations if _api_type(c) in types])

    def iter_members(self, channel_id):
        return iter(self.members.get(channel_id, []))

    def iter_history(self, channel_id, oldest=None):
        self.calls.append(("history", channel_id, oldest))
        if channel_id in self.failing_channels:
            raise self.failing_channels[channel_id]
        return iter(self.history.get(channel_id, []))

    def iter_replies(self, channel_id, thread_ts):
        self.calls.append(("replies", channel_id, thread_ts))
        return iter(self.replies.get((channel_id, thread_ts), []))

    def download(self, url, dest, expected_mimetype=None):
        self.calls.append(("download", url))
        if url in self.failing_files:
            raise DownloadError("HTTP 403")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"contenu de {url}".encode())

    def calls_named(self, name):
        return [c for c in self.calls if c[0] == name]
````

- [ ] **Step 2 : Écrire les tests (qui échouent) — `tests/test_exporter.py`**

````python
import pytest
from fakes import FakeApi, msg, slack_file

from slack_exporter.exporter import ExportOptions, conversation_type, export
from slack_exporter.slack_api import ApiError, AuthError
from slack_exporter.storage import Archive

GENERAL = {"id": "C1", "name": "general", "is_channel": True, "is_member": True}
SECRET = {"id": "G1", "name": "secret", "is_private": True, "is_member": True}
NOT_JOINED = {"id": "C2", "name": "random", "is_channel": True, "is_member": False}
DM = {"id": "D1", "is_im": True, "user": "U2"}
GROUP = {"id": "M1", "name": "mpdm-a--b--c-1", "is_mpim": True, "is_private": True}


def run(api, tmp_path, **options):
    archive = Archive(tmp_path / "archive")
    logs = []
    result = export(api, archive, ExportOptions(**options), log=logs.append)
    return archive, result, logs


def test_conversation_type():
    assert conversation_type(GENERAL) == "public"
    assert conversation_type(SECRET) == "private"
    assert conversation_type(DM) == "im"
    assert conversation_type(GROUP) == "mpim"


def test_writes_meta_users_and_selected_conversations(tmp_path):
    api = FakeApi(
        users=[{"id": "U1", "name": "moi"}, {"id": "U2", "name": "bob"}],
        conversations=[GENERAL, SECRET, NOT_JOINED, DM, GROUP],
        members={"M1": ["U1", "U2", "U3"]},
    )
    archive, result, _ = run(api, tmp_path)

    meta = archive.read_json("meta.json")
    assert meta["format_version"] == 1
    assert meta["team"] == "Acme"
    assert meta["user_id"] == "U1"
    assert meta["errors"] == []
    assert archive.read_json("users.json") == api.users
    assert archive.read_json("channels.json") == [
        {"id": "C1", "name": "general", "type": "public", "members": []},
        {"id": "G1", "name": "secret", "type": "private", "members": []},
        {"id": "D1", "name": "", "type": "im", "members": ["U2"]},
        {"id": "M1", "name": "mpdm-a--b--c-1", "type": "mpim", "members": ["U1", "U2", "U3"]},
    ]
    assert result.conversations == 4


def test_messages_are_stored_in_chronological_order(tmp_path):
    api = FakeApi(conversations=[GENERAL], history={"C1": [msg("3.0", "c"), msg("1.0", "a"), msg("2.0", "b")]})
    archive, result, logs = run(api, tmp_path)

    assert [m["text"] for m in archive.read_messages("C1")] == ["a", "b", "c"]
    assert result.messages == 3
    assert logs == ["✓ general : 3 messages, 0 fichiers"]


def test_thread_replies_are_stored_without_parent(tmp_path):
    parent = msg("1.0", "question", reply_count=2, thread_ts="1.0")
    api = FakeApi(
        conversations=[GENERAL],
        history={"C1": [parent]},
        replies={("C1", "1.0"): [parent, msg("1.1", "r1", thread_ts="1.0"), msg("1.2", "r2", thread_ts="1.0")]},
    )
    archive, _, _ = run(api, tmp_path)

    assert [r["text"] for r in archive.read_replies("C1", "1.0")] == ["r1", "r2"]


def test_type_filter_and_only_filter(tmp_path):
    api = FakeApi(conversations=[GENERAL, SECRET, DM])
    archive, _, _ = run(api, tmp_path, types=("public", "private"), only=("secret",))

    assert [c["id"] for c in archive.read_json("channels.json")] == ["G1"]
    assert api.calls_named("conversations") == [("conversations", ("public_channel", "private_channel"))]


def test_only_accepts_channel_id(tmp_path):
    api = FakeApi(conversations=[GENERAL, DM])
    archive, _, _ = run(api, tmp_path, only=("D1",))
    assert [c["id"] for c in archive.read_json("channels.json")] == ["D1"]


def test_since_is_passed_as_oldest(tmp_path):
    api = FakeApi(conversations=[GENERAL])
    run(api, tmp_path, since=1700000000.0)
    assert api.calls_named("history") == [("history", "C1", "1700000000.000000")]


def test_files_from_messages_and_replies_are_downloaded(tmp_path):
    parent = msg("1.0", "voir pj", reply_count=1, files=[slack_file("F1", "rapport.pdf")])
    reply = msg("1.1", "et ça", thread_ts="1.0", files=[slack_file("F2", "photo.png", "image/png")])
    deleted = msg("2.0", "", files=[{"id": "F3", "mode": "tombstone"}])
    external = msg("3.0", "", files=[slack_file("F4", mode="external")])
    api = FakeApi(
        conversations=[GENERAL],
        history={"C1": [external, deleted, parent]},
        replies={("C1", "1.0"): [parent, reply]},
    )
    archive, result, _ = run(api, tmp_path)

    assert archive.attachment_path("C1", {"id": "F1", "name": "rapport.pdf"}).exists()
    assert archive.attachment_path("C1", {"id": "F2", "name": "photo.png"}).exists()
    assert len(api.calls_named("download")) == 2
    assert result.files == 2


def test_file_error_is_recorded_and_export_continues(tmp_path):
    bad = slack_file("F1")
    api = FakeApi(
        conversations=[GENERAL],
        history={"C1": [msg("1.0", files=[bad]), msg("2.0", files=[slack_file("F2")])]},
        failing_files={bad["url_private_download"]},
    )
    archive, result, _ = run(api, tmp_path)

    assert result.errors == [{"channel": "C1", "file": "F1", "error": "HTTP 403"}]
    assert archive.read_json("meta.json")["errors"] == result.errors
    assert result.files == 1
    assert archive.load_state()["completed"] == ["C1"]


def test_channel_api_error_is_recorded_and_other_channels_exported(tmp_path):
    api = FakeApi(
        conversations=[GENERAL, SECRET],
        history={"G1": [msg("1.0", "ok")]},
        failing_channels={"C1": ApiError("not_in_channel")},
    )
    archive, result, logs = run(api, tmp_path)

    assert result.errors == [{"channel": "C1", "error": "not_in_channel"}]
    assert archive.read_messages("G1") == [msg("1.0", "ok")]
    assert "! general : not_in_channel" in logs
    assert archive.load_state() == {"completed": ["G1"], "in_progress": None}


def test_auth_error_aborts_export(tmp_path):
    api = FakeApi(conversations=[GENERAL], failing_channels={"C1": AuthError("token_revoked")})
    with pytest.raises(AuthError):
        run(api, tmp_path)


def test_resume_skips_completed_and_redoes_interrupted(tmp_path):
    history = {"C1": [msg("1.0", "a", files=[slack_file("F1")])], "G1": [msg("2.0", "b")]}
    first = FakeApi(conversations=[GENERAL, SECRET], history=history,
                    failing_channels={"G1": KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        run(first, tmp_path)
    archive = Archive(tmp_path / "archive")
    assert archive.load_state() == {"completed": ["C1"], "in_progress": "G1"}

    second = FakeApi(conversations=[GENERAL, SECRET], history=history)
    _, result, logs = run(second, tmp_path)

    assert second.calls_named("history") == [("history", "G1", None)]
    assert second.calls_named("download") == []
    assert logs[0] == "= general : déjà exporté"
    assert archive.read_messages("G1") == [msg("2.0", "b")]
    assert archive.load_state() == {"completed": ["C1", "G1"], "in_progress": None}


def test_existing_files_are_kept_unless_refresh(tmp_path):
    history = {"C1": [msg("1.0", files=[slack_file("F1")])]}
    run(FakeApi(conversations=[GENERAL], history=history), tmp_path)

    again = FakeApi(conversations=[GENERAL], history=history)
    Archive(tmp_path / "archive").save_state({"completed": [], "in_progress": None})
    _, result, _ = run(again, tmp_path)
    assert again.calls_named("download") == []
    assert result.files == 1

    refreshed = FakeApi(conversations=[GENERAL], history=history)
    run(refreshed, tmp_path, refresh=True)
    assert len(refreshed.calls_named("download")) == 1


def test_meta_is_written_before_conversations_are_exported(tmp_path):
    api = FakeApi(conversations=[GENERAL], failing_channels={"C1": KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        run(api, tmp_path)
    assert Archive(tmp_path / "archive").read_json("meta.json")["format_version"] == 1
````

- [ ] **Step 3 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_exporter.py -q`
Expected : FAIL (ModuleNotFoundError: No module named 'slack_exporter.exporter')

- [ ] **Step 4 : Implémenter `src/slack_exporter/exporter.py`**

````python
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
    result = ExportResult()
    _write_meta(archive, identity, result.errors)

    archive.write_json("users.json", list(api.iter_users()))
    conversations = _select_conversations(api, options)
    archive.write_json("channels.json", conversations)

    state = {"completed": [], "in_progress": None} if options.refresh else archive.load_state()
    for conv in conversations:
        channel_id = conv["id"]
        label = conv["name"] or channel_id
        if channel_id in state["completed"]:
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


def _select_conversations(api: Any, options: ExportOptions) -> list[dict]:
    selected = []
    for conv in api.iter_conversations([_API_TYPES[t] for t in options.types]):
        kind = conversation_type(conv)
        if kind not in options.types:
            continue
        if kind in ("public", "private") and not conv.get("is_member"):
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
````

- [ ] **Step 5 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 6 : Commit**

```bash
git add src/slack_exporter/exporter.py tests/fakes.py tests/test_exporter.py
git commit -m "feat: export conversations, threads and files with resume support" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5 : Rendu HTML statique

**Files:**
- Create: `src/slack_exporter/renderer.py`
- Create: `src/slack_exporter/templates/index.html`
- Create: `src/slack_exporter/templates/conversation.html`
- Create: `src/slack_exporter/templates/style.css`
- Test: `tests/test_renderer.py`

**Interfaces:**
- Consumes : `Archive`, `FORMAT_VERSION`, `safe_join` (Task 1) ; `FormatContext`, `mrkdwn_to_html`, `emoji_to_unicode` (Task 2) ; contrats `meta.json`/`channels.json` (Task 4) ; `msg`, `slack_file` de `tests/fakes.py` (Task 4).
- Produces (`slack_exporter.renderer`) :
  - `PAGE_THRESHOLD = 5000`, `TYPE_TITLES`
  - `class RenderError(Exception)`
  - `user_display_names(users: list[dict]) -> dict[str, str]`
  - `conversation_title(conv: dict, names: dict[str, str], self_id: str | None) -> str`
  - `french_date(dt: datetime) -> str`
  - `render(archive_dir: Path, site_dir: Path, *, tz: tzinfo | None = None, page_threshold: int = PAGE_THRESHOLD) -> int` (nombre de conversations)
  - Structure produite : `site/index.html`, `site/assets/style.css`, `site/c/<id>/index.html`, `site/c/<id>/<année>.html` (si découpée), `site/c/<id>/files/*`

- [ ] **Step 1 : Écrire les tests (qui échouent) — `tests/test_renderer.py`**

````python
from datetime import timezone

import pytest
from fakes import msg, slack_file

from slack_exporter.renderer import RenderError, conversation_title, french_date, render
from slack_exporter.storage import Archive

USERS = [
    {"id": "U1", "name": "moi", "profile": {"display_name": "Moi"}},
    {"id": "U2", "name": "alice", "profile": {"display_name": "", "real_name": "Alice Martin"}},
    {"id": "U3", "name": "bob", "profile": {}},
]
CHANNELS = [
    {"id": "C1", "name": "general", "type": "public", "members": []},
    {"id": "G1", "name": "secret", "type": "private", "members": []},
    {"id": "D1", "name": "", "type": "im", "members": ["U2"]},
    {"id": "M1", "name": "mpdm-x", "type": "mpim", "members": ["U1", "U2", "U3"]},
]


def make_archive(tmp_path, messages=None, replies=None, channels=CHANNELS, errors=()):
    archive = Archive(tmp_path / "archive")
    archive.write_json("meta.json", {
        "format_version": 1, "team": "Acme", "user_id": "U1",
        "exported_at": "2024-03-10T12:00:00+00:00", "errors": list(errors),
    })
    archive.write_json("users.json", USERS)
    archive.write_json("channels.json", channels)
    for channel_id, items in (messages or {}).items():
        archive.write_messages(channel_id, items)
    for (channel_id, ts), items in (replies or {}).items():
        archive.write_replies(channel_id, ts, items)
    return archive


def do_render(tmp_path, **kwargs):
    site = tmp_path / "site"
    count = render(tmp_path / "archive", site, tz=timezone.utc, **kwargs)
    return site, count


def page(site, channel_id, name="index.html"):
    return (site / "c" / channel_id / name).read_text(encoding="utf-8")


def test_rejects_missing_or_unknown_archive(tmp_path):
    with pytest.raises(RenderError, match="meta.json"):
        render(tmp_path / "vide", tmp_path / "site")
    archive = Archive(tmp_path / "archive")
    archive.write_json("meta.json", {"format_version": 99})
    with pytest.raises(RenderError, match="99"):
        render(tmp_path / "archive", tmp_path / "site")


def test_conversation_titles():
    names = {"U1": "Moi", "U2": "Alice", "U3": "bob"}
    assert conversation_title(CHANNELS[0], names, "U1") == "#general"
    assert conversation_title(CHANNELS[2], names, "U1") == "Alice"
    assert conversation_title(CHANNELS[3], names, "U1") == "Alice, bob"
    assert conversation_title({"id": "D9", "name": "", "type": "im", "members": []}, names, "U1") == "D9"


def test_french_date():
    from datetime import datetime
    assert french_date(datetime(2024, 3, 9)) == "samedi 9 mars 2024"


def test_index_groups_conversations_by_type(tmp_path):
    make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "a"), msg("1710090000.0", "b")]})
    site, count = do_render(tmp_path)

    index = (site / "index.html").read_text(encoding="utf-8")
    assert count == 4
    assert (site / "assets" / "style.css").exists()
    assert index.index("Canaux publics") < index.index("Canaux privés") < index.index("Messages directs") < index.index("Groupes")
    assert '<a href="c/C1/index.html">#general</a>' in index
    assert "2 messages · 2024-03-09 → 2024-03-10" in index
    assert '<a href="c/D1/index.html">Alice Martin</a>' in index
    assert "Alice Martin, bob" in index


def test_index_lists_export_errors(tmp_path):
    make_archive(tmp_path, errors=[{"channel": "C1", "file": "F1", "error": "HTTP 403"}])
    site, _ = do_render(tmp_path)
    assert "C1 / fichier F1 : HTTP 403" in (site / "index.html").read_text(encoding="utf-8")


def test_conversation_page_shows_day_author_time_and_formatted_text(tmp_path):
    make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "*salut* <@U2>", user="U1")]})
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert "samedi 9 mars 2024" in html
    assert '<span class="author">Moi</span>' in html
    assert ">16:00</time>" in html
    assert '<strong>salut</strong> <span class="mention">@Alice Martin</span>' in html
    assert 'href="../../assets/style.css"' in html


def test_message_text_is_escaped(tmp_path):
    make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "&lt;script&gt;alert(1)&lt;/script&gt;")]})
    site, _ = do_render(tmp_path)
    assert "<script>" not in page(site, "C1")


def test_thread_replies_rendered_in_details(tmp_path):
    parent = msg("1710000000.0", "question", reply_count=2)
    make_archive(
        tmp_path,
        messages={"C1": [parent]},
        replies={("C1", "1710000000.0"): [msg("1710000060.0", "r1", user="U2"), msg("1710000120.0", "r2")]},
    )
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert "<summary>2 réponses</summary>" in html
    assert html.index("<details") < html.index("r1") < html.index("r2") < html.index("</details>")


def test_files_are_copied_and_linked(tmp_path):
    image = slack_file("F1", "photo.png", "image/png")
    doc = slack_file("F2", "rapport.pdf", size=3 * 1024 * 1024)
    missing = slack_file("F3", "perdu.zip")
    archive = make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "pj", files=[image, doc, missing])]})
    for f in (image, doc):
        path = archive.attachment_path("C1", f)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert (site / "c" / "C1" / "files" / "F1_photo.png").exists()
    assert '<img src="files/F1_photo.png" alt="photo.png"' in html
    assert '<a href="files/F2_rapport.pdf">📎 rapport.pdf</a> <span class="meta">3.0 Mo</span>' in html
    assert "📎 perdu.zip — fichier non disponible" in html


def test_reactions_and_edited_flag(tmp_path):
    message = msg("1710000000.0", "ok", edited={"ts": "1710000100.0"},
                  reactions=[{"name": "+1", "count": 3}, {"name": "perso", "count": 1}])
    make_archive(tmp_path, messages={"C1": [message]})
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert "(modifié)" in html
    assert '<span class="reaction">👍 3</span>' in html
    assert '<span class="reaction">:perso: 1</span>' in html


def test_messages_without_user_have_an_author(tmp_path):
    messages = [
        {"type": "message", "ts": "1710000000.0", "text": "déployé", "subtype": "bot_message",
         "bot_profile": {"name": "CI Bot"}},
        {"type": "message", "ts": "1710000001.0", "text": "hook", "username": "webhook"},
        {"type": "message", "ts": "1710000002.0", "text": "", "attachments": [{"fallback": "Nouveau ticket #42"}]},
        msg("1710000003.0", "qui ?", user="U_INCONNU"),
    ]
    make_archive(tmp_path, messages={"C1": messages})
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert '<span class="author">CI Bot</span>' in html
    assert '<span class="author">webhook</span>' in html
    assert '<span class="author">Slack</span>' in html
    assert "Nouveau ticket #42" in html
    assert '<span class="author">U_INCONNU</span>' in html


def test_channel_mentions_link_between_exported_pages(tmp_path):
    make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "voir <#G1|secret>")]})
    site, _ = do_render(tmp_path)

    html = page(site, "C1")
    assert '<a class="channel" href="../G1/index.html">#secret</a>' in html
    assert (site / "c" / "G1" / "index.html").exists()


def test_conversation_without_messages(tmp_path):
    make_archive(tmp_path)
    site, _ = do_render(tmp_path)
    assert "Aucun message exporté." in page(site, "G1")


def test_large_conversation_is_split_by_year(tmp_path):
    messages = [msg("1700000000.0", "2023-a"), msg("1700000100.0", "2023-b"), msg("1710000000.0", "2024-a")]
    make_archive(tmp_path, messages={"C1": messages})
    site, _ = do_render(tmp_path, page_threshold=2)

    summary = page(site, "C1")
    assert '<a href="2023.html">2023</a>' in summary
    assert '<a href="2024.html">2024</a>' in summary
    assert "2023-a" not in summary
    assert "2023-a" in page(site, "C1", "2023.html") and "2024-a" not in page(site, "C1", "2023.html")
    assert "2024-a" in page(site, "C1", "2024.html")
    assert 'class="current">2024</a>' in page(site, "C1", "2024.html")


def test_render_is_idempotent(tmp_path):
    make_archive(tmp_path, messages={"C1": [msg("1710000000.0", "a")]})
    do_render(tmp_path)
    site, count = do_render(tmp_path)
    assert count == 4
````

- [ ] **Step 2 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_renderer.py -q`
Expected : FAIL (ModuleNotFoundError: No module named 'slack_exporter.renderer')

- [ ] **Step 3 : Implémenter `src/slack_exporter/templates/index.html`**

````html
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archive Slack — {{ meta.team or "workspace" }}</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header>
<h1>Archive Slack — {{ meta.team or "workspace" }}</h1>
<p class="meta">Exporté le {{ meta.exported_at }}</p>
</header>
<main>
{% for label, items in groups %}
<section>
<h2>{{ label }}</h2>
<ul class="conversations">
{% for c in items %}
<li><a href="c/{{ c.id }}/index.html">{{ c.title }}</a> <span class="meta">{{ c.count }} message{{ "s" if c.count != 1 }}{% if c.first %} · {{ c.first }} → {{ c.last }}{% endif %}</span></li>
{% endfor %}
</ul>
</section>
{% else %}
<p>Aucune conversation exportée.</p>
{% endfor %}
{% if errors %}
<section class="errors">
<h2>Erreurs pendant l'export</h2>
<ul>
{% for e in errors %}
<li>{{ e.channel }}{% if e.file %} / fichier {{ e.file }}{% endif %} : {{ e.error }}</li>
{% endfor %}
</ul>
</section>
{% endif %}
</main>
</body>
</html>
````

- [ ] **Step 4 : Implémenter `src/slack_exporter/templates/conversation.html`**

````html
{% macro render_message(m) %}
<article class="message">
<div class="head"><span class="author">{{ m.author }}</span> <time datetime="{{ m.dt.isoformat() }}">{{ m.time }}</time>{% if m.edited %} <span class="edited">(modifié)</span>{% endif %}</div>
<div class="text">{{ m.html }}</div>
{% if m.files %}
<ul class="files">
{% for f in m.files %}
{% if f.href and f.is_image %}
<li><a href="{{ f.href }}"><img src="{{ f.href }}" alt="{{ f.name }}" loading="lazy"></a></li>
{% elif f.href %}
<li><a href="{{ f.href }}">📎 {{ f.name }}</a> <span class="meta">{{ f.size }}</span></li>
{% else %}
<li class="missing">📎 {{ f.name }} — fichier non disponible</li>
{% endif %}
{% endfor %}
</ul>
{% endif %}
{% if m.reactions %}
<div class="reactions">{% for r in m.reactions %}<span class="reaction">{{ r.emoji }} {{ r.count }}</span>{% endfor %}</div>
{% endif %}
{% if m.replies %}
<details class="thread">
<summary>{{ m.replies|length }} réponse{{ "s" if m.replies|length > 1 }}</summary>
{% for r in m.replies %}{{ render_message(r) }}{% endfor %}
</details>
{% endif %}
</article>
{% endmacro %}
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<link rel="stylesheet" href="../../assets/style.css">
</head>
<body>
<header>
<a href="../../index.html">← Toutes les conversations</a>
<h1>{{ title }}</h1>
{% if years %}
<nav class="years">{% for y in years %}<a href="{{ y }}.html"{% if y == current_year %} class="current"{% endif %}>{{ y }}</a> {% endfor %}</nav>
{% endif %}
</header>
<main>
{% if years and current_year is none %}
<p>Cette conversation est découpée par année : choisissez une année ci-dessus.</p>
{% elif not days %}
<p>Aucun message exporté.</p>
{% endif %}
{% for day in days %}
<h2 class="day">{{ day.label }}</h2>
{% for m in day.messages %}{{ render_message(m) }}{% endfor %}
{% endfor %}
</main>
</body>
</html>
````

- [ ] **Step 5 : Implémenter `src/slack_exporter/templates/style.css`**

````css
:root {
  --bg: #ffffff;
  --fg: #1d1c1d;
  --muted: #616061;
  --border: #e1e1e1;
  --accent: #1264a3;
  --mention-bg: #e8f5fa;
  --code-bg: #f6f6f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1d21;
    --fg: #d1d2d3;
    --muted: #9a9b9d;
    --border: #35373b;
    --accent: #4aa3df;
    --mention-bg: #1d3446;
    --code-bg: #232529;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  max-width: 900px;
  padding: 16px;
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--accent); }
h1 { font-size: 1.6em; margin: 0.4em 0; }
h2 { font-size: 1.2em; }
.meta { color: var(--muted); font-size: 0.9em; }
.conversations { list-style: none; padding: 0; }
.conversations li { padding: 6px 0; border-bottom: 1px solid var(--border); }
.day {
  position: sticky; top: 0; background: var(--bg);
  border-bottom: 1px solid var(--border); padding: 6px 0; margin-top: 24px;
  font-size: 0.95em; color: var(--muted);
}
.message { padding: 8px 0; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
.message .head { margin-bottom: 2px; }
.author { font-weight: 700; }
time, .edited { color: var(--muted); font-size: 0.85em; }
.mention { background: var(--mention-bg); color: var(--accent); border-radius: 3px; padding: 0 2px; }
code { background: var(--code-bg); border: 1px solid var(--border); border-radius: 3px; padding: 0 3px; font-size: 0.9em; }
pre { background: var(--code-bg); border: 1px solid var(--border); border-radius: 4px; padding: 8px; overflow-x: auto; }
pre code { border: 0; padding: 0; }
blockquote { margin: 4px 0; padding-left: 10px; border-left: 4px solid var(--border); color: var(--muted); }
.files { list-style: none; padding: 0; margin: 6px 0; }
.files img { max-width: 360px; max-height: 240px; border: 1px solid var(--border); border-radius: 4px; }
.missing { color: var(--muted); font-style: italic; }
.reactions { margin-top: 4px; }
.reaction { display: inline-block; border: 1px solid var(--border); border-radius: 12px; padding: 0 8px; margin-right: 4px; font-size: 0.9em; }
.thread { margin: 6px 0 0 16px; }
.thread summary { cursor: pointer; color: var(--accent); }
.thread .message { border-bottom: 0; border-left: 2px solid var(--border); padding-left: 10px; }
.years a { margin-right: 8px; }
.years a.current { font-weight: 700; text-decoration: none; }
.errors { color: #b3261e; }
@media (max-width: 600px) {
  .files img { max-width: 100%; }
}
````

- [ ] **Step 6 : Implémenter `src/slack_exporter/renderer.py`**

````python
"""Génération du site HTML statique à partir de l'archive."""
from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import datetime, tzinfo
from importlib.resources import files as package_files
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from slack_exporter.formatting import FormatContext, emoji_to_unicode, mrkdwn_to_html
from slack_exporter.storage import FORMAT_VERSION, Archive, safe_join

PAGE_THRESHOLD = 5000
TYPE_TITLES = {
    "public": "Canaux publics",
    "private": "Canaux privés",
    "im": "Messages directs",
    "mpim": "Groupes",
}
_DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre")


class RenderError(Exception):
    """Archive illisible ou incompatible."""


def user_display_names(users: list[dict]) -> dict[str, str]:
    names = {}
    for user in users:
        profile = user.get("profile") or {}
        names[user["id"]] = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("real_name")
            or user.get("name")
            or user["id"]
        )
    return names


def conversation_title(conv: dict, names: dict[str, str], self_id: str | None) -> str:
    if conv["type"] in ("public", "private"):
        return f"#{conv['name'] or conv['id']}"
    members = conv.get("members") or []
    others = [m for m in members if m != self_id] or members
    if not others:
        return conv.get("name") or conv["id"]
    return ", ".join(names.get(m, m) for m in others)


def french_date(dt: datetime) -> str:
    return f"{_DAYS[dt.weekday()]} {dt.day} {_MONTHS[dt.month - 1]} {dt.year}"


def render(
    archive_dir: Path,
    site_dir: Path,
    *,
    tz: tzinfo | None = None,
    page_threshold: int = PAGE_THRESHOLD,
) -> int:
    """Génère `site_dir` depuis l'archive ; renvoie le nombre de conversations rendues."""
    archive = Archive(archive_dir)
    meta = archive.read_json("meta.json")
    if meta is None:
        raise RenderError(f"{archive_dir} ne contient pas de meta.json : ce n'est pas une archive d'export.")
    if meta.get("format_version") != FORMAT_VERSION:
        raise RenderError(f"Version d'archive non prise en charge : {meta.get('format_version')!r}.")

    conversations = archive.read_json("channels.json", [])
    names = user_display_names(archive.read_json("users.json", []))
    titles = {c["id"]: conversation_title(c, names, meta.get("user_id")) for c in conversations}
    ctx = FormatContext(
        users=names,
        channels={c["id"]: c["name"] or titles[c["id"]] for c in conversations},
    )
    renderer = _Renderer(archive, Path(site_dir), ctx, names, tz, page_threshold)

    renderer.site_dir.mkdir(parents=True, exist_ok=True)
    (renderer.site_dir / "assets").mkdir(exist_ok=True)
    css = package_files("slack_exporter").joinpath("templates/style.css").read_text(encoding="utf-8")
    (renderer.site_dir / "assets" / "style.css").write_text(css, encoding="utf-8")

    summaries = [renderer.conversation(conv, titles[conv["id"]]) for conv in conversations]
    groups = [
        (label, [s for s in summaries if s["type"] == kind])
        for kind, label in TYPE_TITLES.items()
    ]
    html = renderer.env.get_template("index.html").render(
        meta=meta,
        groups=[(label, items) for label, items in groups if items],
        errors=meta.get("errors", []),
    )
    (renderer.site_dir / "index.html").write_text(html, encoding="utf-8")
    return len(summaries)


class _Renderer:
    def __init__(self, archive, site_dir, ctx, names, tz, page_threshold):
        self.archive = archive
        self.site_dir = site_dir
        self.ctx = ctx
        self.names = names
        self.tz = tz
        self.page_threshold = page_threshold
        self.env = Environment(
            loader=PackageLoader("slack_exporter", "templates"),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def conversation(self, conv: dict, title: str) -> dict:
        channel_id = conv["id"]
        out_dir = safe_join(self.site_dir, "c", channel_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        views = [
            self.message(channel_id, out_dir, m, with_replies=True)
            for m in self.archive.read_messages(channel_id)
        ]
        template = self.env.get_template("conversation.html")
        page = {"title": title}

        if len(views) > self.page_threshold:
            by_year: dict[int, list[dict]] = defaultdict(list)
            for view in views:
                by_year[view["dt"].year].append(view)
            years = sorted(by_year)
            for year in years:
                html = template.render(**page, days=_group_by_day(by_year[year]), years=years, current_year=year)
                (out_dir / f"{year}.html").write_text(html, encoding="utf-8")
            html = template.render(**page, days=[], years=years, current_year=None)
        else:
            html = template.render(**page, days=_group_by_day(views), years=[], current_year=None)
        (out_dir / "index.html").write_text(html, encoding="utf-8")

        return {
            "id": channel_id,
            "type": conv["type"],
            "title": title,
            "count": len(views),
            "first": views[0]["dt"].date().isoformat() if views else None,
            "last": views[-1]["dt"].date().isoformat() if views else None,
        }

    def message(self, channel_id: str, out_dir: Path, message: dict, *, with_replies: bool) -> dict:
        ts = float(message["ts"])
        dt = datetime.fromtimestamp(ts, self.tz) if self.tz else datetime.fromtimestamp(ts).astimezone()
        replies = []
        if with_replies and message.get("reply_count"):
            replies = [
                self.message(channel_id, out_dir, r, with_replies=False)
                for r in self.archive.read_replies(channel_id, message["ts"])
            ]
        return {
            "dt": dt,
            "time": dt.strftime("%H:%M"),
            "author": _author(message, self.names),
            "html": Markup(mrkdwn_to_html(message.get("text") or _attachments_text(message), self.ctx)),
            "files": [
                self.file(channel_id, out_dir, f)
                for f in message.get("files", [])
                if f.get("mode") != "tombstone"
            ],
            "reactions": [
                {"emoji": emoji_to_unicode(r["name"]) or f":{r['name']}:", "count": r.get("count", 0)}
                for r in message.get("reactions", [])
            ],
            "edited": "edited" in message,
            "replies": replies,
        }

    def file(self, channel_id: str, out_dir: Path, file: dict) -> dict:
        view = {
            "name": file.get("name") or file.get("title") or file.get("id", "fichier"),
            "size": _human_size(file.get("size")),
            "is_image": str(file.get("mimetype", "")).startswith("image/"),
            "href": None,
        }
        if "id" not in file:
            return view
        source = self.archive.attachment_path(channel_id, file)
        if source.exists():
            target = out_dir / "files" / source.name
            target.parent.mkdir(exist_ok=True)
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copy2(source, target)
            view["href"] = f"files/{source.name}"
        return view


def _author(message: dict, names: dict[str, str]) -> str:
    if message.get("user"):
        return names.get(message["user"], message["user"])
    if message.get("username"):
        return message["username"]
    bot = message.get("bot_profile") or {}
    return bot.get("name") or "Slack"


def _attachments_text(message: dict) -> str:
    parts = [a.get("text") or a.get("fallback") or "" for a in message.get("attachments", [])]
    return "\n".join(p for p in parts if p)


def _human_size(size: object) -> str:
    if not isinstance(size, (int, float)):
        return ""
    if size < 1024:
        return f"{size:.0f} o"
    for unit in ("Ko", "Mo", "Go"):
        size /= 1024
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}"
    return ""


def _group_by_day(views: list[dict]) -> list[dict]:
    days: list[dict] = []
    for view in views:
        label = french_date(view["dt"])
        if not days or days[-1]["label"] != label:
            days.append({"label": label, "messages": []})
        days[-1]["messages"].append(view)
    return days
````

- [ ] **Step 7 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 8 : Commit**

```bash
git add src/slack_exporter/renderer.py src/slack_exporter/templates tests/test_renderer.py
git commit -m "feat: render archive as static HTML site" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6 : Interface CLI et README

**Files:**
- Create: `src/slack_exporter/cli.py`
- Create: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes : `ConfigError`, `build_api`, `credentials_from_env` (Task 3) ; `AuthError` (Task 3) ; `ALL_TYPES`, `ExportOptions`, `export` (Task 4) ; `RenderError`, `render` (Task 5) ; `Archive` (Task 1) ; `FakeApi` (Task 4).
- Produces : groupe click `slack_exporter.cli.main` avec les commandes `export --out DIR [--types] [--since AAAA-MM-JJ] [--only NOM]... [--refresh]` et `render --archive DIR --site DIR`. Codes de sortie : 0 succès, 1 erreur (config, auth, archive invalide), 2 option invalide. `cli.build_api` est patché dans les tests.

- [ ] **Step 1 : Écrire les tests (qui échouent) — `tests/test_cli.py`**

````python
import pytest
from click.testing import CliRunner
from fakes import FakeApi, msg, slack_file

from slack_exporter import cli
from slack_exporter.slack_api import AuthError

GENERAL = {"id": "C1", "name": "general", "is_channel": True, "is_member": True}
DM = {"id": "D1", "is_im": True, "user": "U2"}


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi(
        users=[{"id": "U1", "name": "moi"}, {"id": "U2", "name": "alice"}],
        conversations=[GENERAL, DM],
        history={
            "C1": [msg("1710000000.0", "bonjour", files=[slack_file("F1")])],
            "D1": [msg("1710000100.0", "coucou", user="U2")],
        },
    )
    received = {}

    def fake_build_api(credentials, **kwargs):
        received["credentials"] = credentials
        return api

    monkeypatch.setattr(cli, "build_api", fake_build_api)
    api.received = received
    return api


def invoke(args, env=None):
    return CliRunner().invoke(cli.main, args, env=env or {"SLACK_TOKEN": "xoxp-test"})


def test_export_requires_token(tmp_path):
    result = CliRunner().invoke(cli.main, ["export", "--out", str(tmp_path / "a")], env={"SLACK_TOKEN": ""})
    assert result.exit_code == 1
    assert "SLACK_TOKEN" in result.output


def test_export_rejects_unknown_type(tmp_path, fake_api):
    result = invoke(["export", "--out", str(tmp_path / "a"), "--types", "public,dm"])
    assert result.exit_code == 2
    assert "public, private, im, mpim" in result.output


def test_export_writes_archive_and_summary(tmp_path, fake_api):
    result = invoke(["export", "--out", str(tmp_path / "a")])

    assert result.exit_code == 0, result.output
    assert "✓ general : 1 messages, 1 fichiers" in result.output
    assert "Export terminé : 2 conversations, 2 messages, 1 fichiers." in result.output
    assert (tmp_path / "a" / "conversations" / "C1" / "messages.jsonl").exists()
    assert fake_api.received["credentials"].token == "xoxp-test"


def test_export_passes_filters(tmp_path, fake_api):
    result = invoke([
        "export", "--out", str(tmp_path / "a"),
        "--types", "public", "--since", "2023-11-14", "--only", "#general",
    ])

    assert result.exit_code == 0, result.output
    assert fake_api.calls_named("conversations") == [("conversations", ("public_channel",))]
    assert fake_api.calls_named("history") == [("history", "C1", "1699920000.000000")]


def test_export_reports_errors(tmp_path, fake_api):
    fake_api.failing_files = {slack_file("F1")["url_private_download"]}
    result = invoke(["export", "--out", str(tmp_path / "a")])

    assert result.exit_code == 0
    assert "1 erreur(s)" in result.output
    assert "C1 / fichier F1 : HTTP 403" in result.output


def test_export_auth_error_is_reported(tmp_path, fake_api):
    fake_api.failing_channels = {"C1": AuthError("token_revoked")}
    result = invoke(["export", "--out", str(tmp_path / "a")])

    assert result.exit_code == 1
    assert "token_revoked" in result.output


def test_export_then_render_end_to_end(tmp_path, fake_api):
    assert invoke(["export", "--out", str(tmp_path / "a")]).exit_code == 0
    result = invoke(["render", "--archive", str(tmp_path / "a"), "--site", str(tmp_path / "site")])

    assert result.exit_code == 0, result.output
    assert "2 conversations rendues" in result.output
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "#general" in index and "alice" in index
    assert (tmp_path / "site" / "c" / "C1" / "files" / "F1_doc.pdf").exists()


def test_render_rejects_non_archive(tmp_path):
    (tmp_path / "vide").mkdir()
    result = invoke(["render", "--archive", str(tmp_path / "vide"), "--site", str(tmp_path / "site")])
    assert result.exit_code == 1
    assert "meta.json" in result.output
````

- [ ] **Step 2 : Vérifier que les tests échouent**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected : FAIL (ImportError: cannot import name 'cli' from 'slack_exporter')

- [ ] **Step 3 : Implémenter `src/slack_exporter/cli.py`**

````python
"""Interface en ligne de commande : `slack-exporter export` et `slack-exporter render`."""
from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path

import click

from slack_exporter.auth import ConfigError, build_api, credentials_from_env
from slack_exporter.exporter import ALL_TYPES, ExportOptions
from slack_exporter.exporter import export as run_export
from slack_exporter.renderer import RenderError
from slack_exporter.renderer import render as run_render
from slack_exporter.slack_api import AuthError
from slack_exporter.storage import Archive


def _parse_types(value: str) -> tuple[str, ...]:
    types = tuple(t.strip() for t in value.split(",") if t.strip())
    unknown = [t for t in types if t not in ALL_TYPES]
    if not types or unknown:
        raise click.BadParameter(
            f"valeur invalide {value!r} ; types possibles : {', '.join(ALL_TYPES)}",
            param_hint="--types",
        )
    return types


@click.group()
def main() -> None:
    """Exporte vos conversations Slack et les convertit en archive HTML consultable."""


@main.command()
@click.option("--out", "out_dir", required=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Dossier de l'archive (créé si besoin, réutilisé pour reprendre).")
@click.option("--types", default=",".join(ALL_TYPES), show_default=True,
              help="Types de conversations, séparés par des virgules.")
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
              help="N'exporter que les messages à partir de cette date (AAAA-MM-JJ, UTC).")
@click.option("--only", multiple=True,
              help="Nom ou identifiant d'une conversation à exporter (répétable).")
@click.option("--refresh", is_flag=True,
              help="Tout réexporter, y compris les conversations et fichiers déjà récupérés.")
def export(out_dir: Path, types: str, since, only: tuple[str, ...], refresh: bool) -> None:
    """Exporte les conversations depuis Slack vers OUT_DIR.

    Le token est lu dans SLACK_TOKEN (xoxp-… ou xoxc-…), et le cookie de session
    dans SLACK_COOKIE_D pour un token xoxc-.
    """
    try:
        credentials = credentials_from_env(os.environ)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    options = ExportOptions(
        types=_parse_types(types),
        since=since.replace(tzinfo=timezone.utc).timestamp() if since else None,
        only=tuple(o.lstrip("#") for o in only),
        refresh=refresh,
    )
    try:
        result = run_export(build_api(credentials), Archive(out_dir), options, log=click.echo)
    except AuthError as exc:
        raise click.ClickException(
            f"Slack a refusé l'authentification ({exc.code}). Vérifiez SLACK_TOKEN et SLACK_COOKIE_D."
        ) from exc
    click.echo(
        f"Export terminé : {result.conversations} conversations, "
        f"{result.messages} messages, {result.files} fichiers."
    )
    if result.errors:
        click.echo(f"{len(result.errors)} erreur(s), détaillées dans {out_dir / 'meta.json'} :", err=True)
        for error in result.errors:
            target = f"{error['channel']} / fichier {error['file']}" if "file" in error else error["channel"]
            click.echo(f"  - {target} : {error['error']}", err=True)


@main.command()
@click.option("--archive", "archive_dir", required=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Dossier produit par `export`.")
@click.option("--site", "site_dir", required=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Dossier du site HTML à générer.")
def render(archive_dir: Path, site_dir: Path) -> None:
    """Génère le site HTML consultable à partir d'une archive."""
    try:
        count = run_render(archive_dir, site_dir)
    except RenderError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{count} conversations rendues : ouvrez {site_dir / 'index.html'}")
````

- [ ] **Step 4 : Implémenter `README.md`**

````markdown
# slack-exporter

Exporte toutes vos conversations Slack (canaux publics dont vous êtes membre,
canaux privés, messages directs, groupes), pièces jointes comprises, puis génère
une archive HTML consultable hors ligne, à déposer dans Microsoft Teams ou OneDrive.

## Installation

```bash
uv venv .venv && uv pip install -e '.[dev]'
# ou : python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Obtenir un token

**Option 1 : token d'app utilisateur (`xoxp-…`)**, si votre workspace autorise l'installation d'apps.

1. Sur <https://api.slack.com/apps>, cliquez sur *Create New App* → *From scratch* et choisissez votre workspace.
2. Dans *OAuth & Permissions* → *User Token Scopes*, ajoutez :
   `channels:read`, `channels:history`, `groups:read`, `groups:history`,
   `im:read`, `im:history`, `mpim:read`, `mpim:history`, `users:read`, `files:read`.
3. Cliquez sur *Install to Workspace*, puis copiez le *User OAuth Token*.

**Option 2 : session navigateur (`xoxc-…` + cookie `d`)**, si l'installation d'apps est bloquée.
Vérifiez d'abord que cet usage est conforme aux règles de votre organisation.

1. Ouvrez Slack dans le navigateur, puis les outils de développement (F12).
2. Dans la console, exécutez
   `JSON.parse(localStorage.localConfig_v2).teams[Object.keys(JSON.parse(localStorage.localConfig_v2).teams)[0]].token` :
   le résultat est le token `xoxc-…` (cette méthode peut changer avec les versions de Slack).
3. Dans *Application* → *Cookies* → `https://app.slack.com`, copiez la valeur du cookie `d` (commence par `xoxd-`).

## Utilisation

```bash
export SLACK_TOKEN=xoxp-...          # ou xoxc-...
export SLACK_COOKIE_D=xoxd-...       # seulement avec un token xoxc-

.venv/bin/slack-exporter export --out ./archive
.venv/bin/slack-exporter render --archive ./archive --site ./site
```

Ouvrez ensuite `site/index.html`.

- L'export est **reprenable** : en cas d'interruption, relancez la même commande.
  Les conversations terminées sont sautées et les fichiers déjà téléchargés sont conservés.
- Options d'`export` : `--types public,private,im,mpim`, `--since AAAA-MM-JJ`,
  `--only <nom|id>` (répétable) et `--refresh` pour tout reprendre de zéro.
- Les erreurs non bloquantes (conversation inaccessible, fichier introuvable) sont
  listées à la fin, dans `archive/meta.json` et sur la page d'accueil du site.

## Déposer l'archive dans Teams

Zippez le dossier `site/`, puis déposez-le dans l'onglet *Fichiers* d'un canal Teams
ou dans OneDrive. Une fois décompressé, ouvrez `index.html` dans le navigateur.
Tous les liens sont relatifs et la page n'utilise ni JavaScript ni ressource externe.

## Tests

```bash
.venv/bin/pytest
```
````

- [ ] **Step 5 : Vérifier que les tests passent**

Run: `.venv/bin/pytest -q`
Expected : PASS, toute la suite (aucune régression sur les tâches précédentes)

- [ ] **Step 6 : Commit**

```bash
git add src/slack_exporter/cli.py README.md tests/test_cli.py
git commit -m "feat: add export/render CLI and README" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7 : Vérification finale

- [ ] **Step 1 : Suite complète et aide de la CLI**

```bash
.venv/bin/pytest -q
.venv/bin/slack-exporter --help
.venv/bin/slack-exporter export --help
```
Expected : 99 tests passent ; l'aide liste `export` et `render` avec leurs options.

- [ ] **Step 2 : Essai réel (par l'utilisateur, avec son token)**

```bash
export SLACK_TOKEN=xoxp-...   # ou xoxc-... avec SLACK_COOKIE_D
.venv/bin/slack-exporter export --out ./archive --only <un-petit-canal>
.venv/bin/slack-exporter render --archive ./archive --site ./site
```
Expected : `site/index.html` s'ouvre ; la conversation affiche les auteurs, les fils, les pièces jointes et les réactions. Relancer `export` affiche `= <canal> : déjà exporté`. Lancer ensuite l'export complet sans `--only`.
