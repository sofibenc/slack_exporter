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
    assert logs[-1] == "✓ general : 3 messages, 0 fichiers"


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
    assert "= general : déjà exporté" in logs
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


def test_rerun_retries_failed_files_of_completed_conversations(tmp_path):
    bad = slack_file("F1")
    history = {"C1": [msg("1.0", files=[bad])]}
    _, first, _ = run(FakeApi(conversations=[GENERAL], history=history,
                              failing_files={bad["url_private_download"]}), tmp_path)
    assert first.errors == [{"channel": "C1", "file": "F1", "error": "HTTP 403"}]

    second = FakeApi(conversations=[GENERAL], history=history)
    archive, result, _ = run(second, tmp_path)

    assert second.calls_named("history") == []
    assert second.calls_named("download") == [("download", bad["url_private_download"])]
    assert archive.attachment_path("C1", bad).exists()
    assert archive.read_json("meta.json")["errors"] == []


def test_rerun_keeps_reporting_files_that_still_fail(tmp_path):
    bad = slack_file("F1")
    parent = msg("1.0", reply_count=1)
    reply = msg("1.1", thread_ts="1.0", files=[bad])
    kwargs = dict(conversations=[GENERAL], history={"C1": [parent]},
                  replies={("C1", "1.0"): [parent, reply]},
                  failing_files={bad["url_private_download"]})
    run(FakeApi(**kwargs), tmp_path)

    archive, result, _ = run(FakeApi(**kwargs), tmp_path)

    assert result.errors == [{"channel": "C1", "file": "F1", "error": "HTTP 403"}]
    assert archive.read_json("meta.json")["errors"] == result.errors


def test_rerun_with_only_keeps_previously_exported_conversations(tmp_path):
    run(FakeApi(conversations=[GENERAL, SECRET]), tmp_path)
    renamed = dict(SECRET, name="secret-v2")

    archive, _, _ = run(FakeApi(conversations=[GENERAL, renamed]), tmp_path, only=("secret-v2",))

    assert [(c["id"], c["name"]) for c in archive.read_json("channels.json")] == [
        ("C1", "general"), ("G1", "secret-v2"),
    ]


def test_logs_progress_of_each_phase(tmp_path):
    api = FakeApi(users=[{"id": "U1"}, {"id": "U2"}], conversations=[GENERAL, NOT_JOINED, DM])
    _, _, logs = run(api, tmp_path)

    assert logs[:3] == [
        "Connecté à Acme en tant que moi",
        "Utilisateurs : 2",
        "Conversations à exporter : 2",
    ]


def test_conversation_without_is_member_flag_is_kept(tmp_path):
    # users.conversations ne renvoie que des conversations dont on est membre,
    # sans toujours préciser is_member.
    api = FakeApi(conversations=[{"id": "C3", "name": "projet", "is_channel": True}])
    archive, _, _ = run(api, tmp_path)
    assert [c["id"] for c in archive.read_json("channels.json")] == ["C3"]
