from datetime import datetime, timezone

from app.platform import runtime
from app.platform.store import cron_matches, next_cron


def test_cron_matching_and_next_occurrence():
    value = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    assert cron_matches("0 8 * * 1-5", value)
    next_value = next_cron("*/15 * * * *", value)
    assert next_value == datetime(2026, 8, 24, 8, 15, tzinfo=timezone.utc)


def test_effective_route_uses_durable_route_event(monkeypatch):
    class Store:
        def events_after(self, run_id, event_id):
            assert run_id == "run-1"
            assert event_id == 0
            return [
                {"event_type": "queued", "payload": {}},
                {
                    "event_type": "route_selected",
                    "payload": {"model": "qwen3-coder", "workflow": "code"},
                },
            ]

    monkeypatch.setattr(runtime, "get_run_store", lambda: Store())
    assert runtime._effective_route("run-1", "orchestrator") == "qwen3-coder"


def test_incorrect_completion_uses_answer_audit(monkeypatch):
    class Store:
        def events_after(self, run_id, event_id):
            return [{"event_type": "answer_audit_failed", "payload": {}}]

    monkeypatch.setattr(runtime, "get_run_store", lambda: Store())
    assert runtime._incorrect_completion("run-2", "completed")
    assert not runtime._incorrect_completion("run-2", "failed")
