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
            "url": "https://acme.slack.com/", "user": "moi", "user_id": "U1",
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
