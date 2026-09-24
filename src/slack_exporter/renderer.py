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
