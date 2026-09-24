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
