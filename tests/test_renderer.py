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
