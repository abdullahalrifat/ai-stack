from app.tools import web_fetch


class DummyResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def iter_content(self, chunk_size):
        return iter(self.chunks)


def test_read_bounded_uses_configured_download_limit(monkeypatch):
    monkeypatch.setattr(web_fetch, "WEB_FETCH_MAX_BYTES", 3)

    try:
        web_fetch._read_bounded(DummyResponse([b"ab", b"cd"]))
    except ValueError as exc:
        assert "configured retrieval limit" in str(exc)
    else:
        raise AssertionError("expected configured byte limit to be enforced")
