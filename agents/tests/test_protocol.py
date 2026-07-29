import json
from pathlib import Path

import pytest
from aistack_cli import protocol as cli_protocol
from fastapi import HTTPException

from app.api import protocol as server_protocol


def test_checked_in_contract_matches_both_implementations():
    contract = json.loads(
        (
            Path(__file__).parents[2] / "contracts" / "aistack-protocol-v1.json"
        ).read_text()
    )

    metadata = contract["x-aistack-protocol"]
    assert contract["openapi"] == "3.1.0"
    assert metadata["current"] == server_protocol.PROTOCOL_VERSION
    assert metadata["current"] == cli_protocol.PROTOCOL_VERSION
    assert metadata["event_schema"] == server_protocol.EVENT_SCHEMA_VERSION
    assert metadata["event_schema"] == cli_protocol.EVENT_SCHEMA_VERSION
    assert {
        "/capabilities",
        "/health",
        "/projects",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/events",
        "/runs/{run_id}/approve",
        "/runs/{run_id}/cancel",
        "/runs/{run_id}/discard",
        "/workspace/choices",
        "/workspace/default",
    } == set(contract["paths"])


def test_server_rejects_explicit_future_protocol():
    with pytest.raises(HTTPException) as error:
        server_protocol.verify_protocol_version("999")

    assert error.value.status_code == 426
    assert "Upgrade the CLI or server" in error.value.detail


def test_unknown_event_fields_are_forward_compatible():
    cli_protocol.validate_event(
        {
            "schema_version": 1,
            "event_type": "planning",
            "future_field": {"safe": True},
        }
    )


def test_unknown_event_schema_fails_safely():
    with pytest.raises(cli_protocol.ProtocolError, match="Upgrade the CLI"):
        cli_protocol.validate_event({"schema_version": 2, "event_type": "planning"})
