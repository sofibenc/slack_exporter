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


def make_api(client=None, session=None, sleeps=None, call_delay=0.0, notify=None):
    sleeps = sleeps if sleeps is not None else []
    return SlackApi(
        client or FakeClient({}),
        session or FakeSession([]),
        call_delay=call_delay,
        sleep=sleeps.append,
        notify=notify or (lambda message: None),
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


def test_conversations_lists_only_the_users_own_conversations():
    # users.conversations ne renvoie que les conversations dont l'utilisateur est membre,
    # au lieu de parcourir tous les canaux publics du workspace.
    client = FakeClient({"users_conversations": [{"channels": [{"id": "C1"}]}]})
    api = make_api(client)

    assert list(api.iter_conversations(["public_channel", "im"])) == [{"id": "C1"}]
    assert client.calls == [("users_conversations", {
        "types": "public_channel,im", "exclude_archived": False, "limit": 200,
    })]


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


def test_download_rate_limit_wait_is_announced(tmp_path):
    notices = []
    session = FakeSession([FakeResponse(status=429, headers={"Retry-After": "30"}), FakeResponse()])
    api = make_api(session=session, notify=notices.append)

    api.download("https://files/F1", tmp_path / "f")

    assert notices == ["Limite Slack atteinte, reprise dans 30 s…"]


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
