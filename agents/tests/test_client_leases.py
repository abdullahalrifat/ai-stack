from unittest.mock import MagicMock

from app.runs import client_leases


def test_expire_abandoned_client_runs_publishes_each_cancellation(monkeypatch):
    class Store:
        def cancel_expired_client_runs(self, limit):
            assert limit == client_leases.CLIENT_LEASE_SWEEP_BATCH_SIZE
            return [
                {"id": "run-1", "status": "cancelling", "overdue_seconds": 1.5},
                {"id": "run-2", "status": "cancelled", "overdue_seconds": 2},
            ]

        def append_event(self, run_id, event_type, payload):
            return {
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
            }

    publisher = MagicMock()
    monkeypatch.setattr(client_leases, "get_run_store", lambda: Store())
    monkeypatch.setattr(
        client_leases,
        "get_event_publisher",
        lambda: publisher,
    )

    assert client_leases.expire_abandoned_client_runs() == 2
    assert publisher.publish.call_count == 2
    first = publisher.publish.call_args_list[0].args[0]
    assert first == {
        "run_id": "run-1",
        "event_type": "client_disconnected",
        "payload": {"status": "cancelling", "overdue_seconds": 1.5},
    }


def test_sweeper_records_database_failures(monkeypatch):
    class Store:
        def cancel_expired_client_runs(self, limit):
            raise RuntimeError("database unavailable")

    metrics = client_leases.LeaseSweepMetrics()
    monkeypatch.setattr(client_leases, "lease_sweep_metrics", metrics)
    monkeypatch.setattr(client_leases, "get_run_store", lambda: Store())

    try:
        client_leases.expire_abandoned_client_runs()
    except RuntimeError:
        pass

    assert metrics.snapshot()["failures_total"] == 1
    assert "database unavailable" in metrics.snapshot()["last_error"]


def test_renewal_failures_are_observable():
    metrics = client_leases.LeaseSweepMetrics()

    metrics.renewal(True)
    metrics.renewal(False)

    assert metrics.snapshot()["renewal_attempts_total"] == 2
    assert metrics.snapshot()["renewal_failures_total"] == 1
