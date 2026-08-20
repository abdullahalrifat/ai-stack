import json

from app.channels import delivery


class Store:
    def __init__(self, items):
        self.items = items
        self.finished = []

    def claim_channel_deliveries(self, limit=20):
        return self.items

    def finish_channel_delivery(self, provider, event_id, error=None):
        self.finished.append((provider, event_id, error))


def test_telegram_delivery_marks_terminal_run_delivered(monkeypatch):
    store = Store(
        [
            {
                "provider": "telegram",
                "event_id": "update-1",
                "identity": "42",
                "run_id": "run-1",
                "answer": "done",
                "error": None,
            }
        ]
    )
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setattr(delivery, "get_run_store", lambda: store)
    monkeypatch.setattr(
        delivery,
        "urlopen",
        lambda request, timeout: requests.append(request) or Response(),
    )

    assert delivery.deliver_pending_once() == 1
    assert store.finished == [("telegram", "update-1", None)]
    payload = json.loads(requests[0].data)
    assert payload == {"chat_id": "42", "text": "done"}


def test_whatsapp_delivery_failure_is_retried(monkeypatch):
    store = Store(
        [
            {
                "provider": "whatsapp",
                "event_id": "message-1",
                "identity": "8801",
                "run_id": "run-2",
                "answer": "done",
                "error": None,
            }
        ]
    )
    monkeypatch.setattr(delivery, "get_run_store", lambda: store)
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

    assert delivery.deliver_pending_once() == 0
    assert store.finished[0][:2] == ("whatsapp", "message-1")
    assert "required" in store.finished[0][2]
