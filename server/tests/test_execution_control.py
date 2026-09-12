from unittest.mock import patch

from fastapi import HTTPException

from app.api.execution_control import CheckpointRequest, SteeringRequest, checkpoint, steer


def test_steering_requires_action_specific_fields():
    with patch("app.api.execution_control.get_run_store") as factory:
        factory.return_value.get_run.return_value = {"id": "run-1"}
        factory.return_value.append_event.return_value = {"ok": True}
        assert steer("run-1", SteeringRequest(action="pause"))["ok"] is True
        try:
            steer("run-1", SteeringRequest(action="redirect"))
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("redirect without instruction should fail")


def test_checkpoint_is_persisted_as_run_event():
    with patch("app.api.execution_control.get_run_store") as factory:
        factory.return_value.get_run.return_value = {"id": "run-1"}
        factory.return_value.append_event.return_value = {"event_type": "checkpoint_created"}
        result = checkpoint("run-1", CheckpointRequest(label="before-tests"))
        assert result["event_type"] == "checkpoint_created"
