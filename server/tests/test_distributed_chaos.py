from app.evals.distributed_chaos import CASES, validate_case


def test_chaos_matrix_covers_required_distributed_faults():
    faults = {case.fault for case in CASES}
    assert {
        "server_restart",
        "worker_restart",
        "redis_outage",
        "postgres_outage",
        "network_partition",
        "lease_expiry",
        "duplicate_completion",
        "cancellation_race",
        "disk_state_failure",
        "telemetry_outage",
    } <= faults


def test_chaos_contract_rejects_unexpected_recovery():
    case = next(item for item in CASES if item.fault == "lease_expiry")
    assert validate_case(case, "stale-worker-rejected")
    assert not validate_case(case, "completed")


def test_chaos_cases_have_unique_ids_and_explicit_expectations():
    assert len({case.id for case in CASES}) == len(CASES)
    assert all(case.expected for case in CASES)
