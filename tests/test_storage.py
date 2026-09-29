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


def test_sanitize_filename_keeps_extension_when_truncating():
    name = sanitize_filename("a" * 300 + ".pdf")
    assert len(name) == 100
    assert name.endswith("a.pdf")


def test_sanitize_filename_strips_trailing_dots():
    # Windows et OneDrive refusent les noms qui finissent par un point.
    assert sanitize_filename("rapport.") == "rapport"
    assert sanitize_filename("x" * 99 + ".") == "x" * 99


def test_canvas_attachments_are_saved_as_html(tmp_path):
    archive = Archive(tmp_path)
    canvas = {"id": "F1", "name": "Maquettage", "filetype": "quip", "mimetype": "application/vnd.slack-docs"}
    assert archive.attachment_path("C1", canvas).name == "F1_Maquettage.html"
    already = dict(canvas, name="Notes.html")
    assert archive.attachment_path("C1", already).name == "F1_Notes.html"
