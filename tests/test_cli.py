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
        received["kwargs"] = kwargs
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
    assert "✓ [1/2] #general : 1 messages, 1 fichiers" in result.output
    assert "Export terminé : 2 conversations, 2 messages, 1 fichiers." in result.output
    assert (tmp_path / "a" / "conversations" / "C1" / "messages.jsonl").exists()
    assert fake_api.received["credentials"].token == "xoxp-test"
    assert "Utilisateurs : 2" in result.output
    assert fake_api.received["kwargs"]["notify"] is not None


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


@pytest.mark.parametrize("extra", [["--refresh"], ["--since", "2024-01-01"]])
def test_update_rejects_incompatible_options(tmp_path, fake_api, extra):
    result = invoke(["export", "--out", str(tmp_path / "a"), "--update", *extra])
    assert result.exit_code == 2
    assert f"--update est incompatible avec {extra[0]}" in result.output


def test_update_rereads_completed_conversations(tmp_path, fake_api):
    assert invoke(["export", "--out", str(tmp_path / "a")]).exit_code == 0
    result = invoke(["export", "--out", str(tmp_path / "a"), "--update"])

    assert result.exit_code == 0, result.output
    assert "↻ [1/2] #general : +0 messages, 0 fils mis à jour, 1 fichiers" in result.output
