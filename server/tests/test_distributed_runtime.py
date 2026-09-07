import time

from app.evals.distributed_runtime import (
    DistributedCase,
    aggregate,
    run_case,
)


def test_distributed_lifecycle_requires_real_completion_state():
    case = DistributedCase(id="task-1", task="fix tests")

    def executor(_case):
        return {
            "states": ["queued", "leased", "running", "verifying", "uploading_result", "completed"],
            "state": "completed",
            "completed": True,
        }

    result = run_case(case, executor)
    assert result.success is True
    assert result.false_completion is False
    assert result.states[-1] == "completed"


def test_distributed_harness_rejects_false_success():
    case = DistributedCase(id="task-2", task="repair bug")

    result = run_case(
        case,
        lambda _case: {
            "states": ["queued", "leased", "running"],
            "state": "running",
            "completed": True,
        },
    )

    assert result.success is False
    assert result.false_completion is True


def test_distributed_harness_records_worker_faults():
    case = DistributedCase(id="task-3", task="run benchmark")

    def executor(_case):
        raise TimeoutError("worker disappeared")

    result = run_case(case, executor)
    assert result.success is False
    assert result.false_completion is False
    assert "TimeoutError" in (result.error or "")


def test_aggregate_reports_success_false_completion_and_median_latency(monkeypatch):
    values = iter([1.0, 2.0, 3.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(values))
    cases = [DistributedCase(id=str(i), task="x") for i in range(3)]
    results = [
        run_case(cases[0], lambda _: {"state": "completed", "completed": True}),
        run_case(cases[1], lambda _: {"state": "running", "completed": True}),
        run_case(cases[2], lambda _: {"state": "failed", "completed": False}),
    ]
    metrics = aggregate(results)
    assert metrics["task_success_rate"] == 1 / 3
    assert metrics["incorrect_completion_rate"] == 1 / 3
    assert metrics["median_latency_seconds"] == 1.0
