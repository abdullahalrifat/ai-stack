"""Server adapter for the shared token-efficient Jarvis runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from jarvis_core import (
    ArtifactResolver,
    FileArtifactStore,
    TokenBudget,
    TokenLedger,
    TokenReservation,
    TraceRecorder,
    Usage,
    compact_messages,
    delta_context,
    summarize_tool_result,
)
from jarvis_core.tokens import estimate_tokens

from ..core.config import (
    ARTIFACT_ROOT,
    TOKEN_AGENT_INPUT_LIMIT,
    TOKEN_AGENT_OUTPUT_LIMIT,
    TOKEN_RUN_INPUT_LIMIT,
    TOKEN_RUN_OUTPUT_LIMIT,
    TOKEN_TURN_INPUT_LIMIT,
    TOKEN_TURN_OUTPUT_LIMIT,
)


@dataclass
class ServerAgentRuntime:
    ledger: TokenLedger
    artifacts: FileArtifactStore
    previous_state: dict[str, Any]
    trace: TraceRecorder

    @classmethod
    def for_state(cls, state) -> "ServerAgentRuntime":
        existing = getattr(state, "shared_runtime", None)
        if isinstance(existing, cls):
            return existing
        runtime = cls(
            ledger=TokenLedger(
                TokenBudget(
                    max_run_input=TOKEN_RUN_INPUT_LIMIT,
                    max_run_output=TOKEN_RUN_OUTPUT_LIMIT,
                    max_turn_input=TOKEN_TURN_INPUT_LIMIT,
                    max_turn_output=TOKEN_TURN_OUTPUT_LIMIT,
                    max_agent_input=TOKEN_AGENT_INPUT_LIMIT,
                    max_agent_output=TOKEN_AGENT_OUTPUT_LIMIT,
                )
            ),
            artifacts=FileArtifactStore(
                ARTIFACT_ROOT
                / hashlib.sha256(
                    str(getattr(state, "conversation_id", id(state))).encode("utf-8")
                ).hexdigest()
            ),
            previous_state={},
            trace=TraceRecorder(),
        )
        state.shared_runtime = runtime
        return runtime

    def reserve_turn(
        self,
        role: str,
        messages: object,
        tools: object,
        max_output_tokens: int,
    ) -> TokenReservation:
        self.trace.record(
            "model_reservation",
            role=role,
            max_output_tokens=max_output_tokens,
        )
        return self.ledger.reserve(
            role,
            estimate_tokens({"messages": messages, "tools": tools}),
            max_output_tokens,
        )

    def record_turn(
        self,
        reservation: TokenReservation,
        role: str,
        model: str,
        messages: object,
        tools: object,
        output: object,
        provider_usage: Mapping[str, int] | None = None,
    ) -> None:
        usage = TokenLedger.usage_from_provider(role, model, provider_usage) or Usage(
            agent=role,
            model=model,
            input_tokens=estimate_tokens({"messages": messages, "tools": tools}),
            output_tokens=estimate_tokens(output),
        )
        self.ledger.commit(reservation, usage)
        self.trace.record(
            "model_usage",
            role=role,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    def refund_turn(self, reservation: TokenReservation) -> None:
        self.ledger.refund(reservation)
        self.trace.record("model_reservation_refunded", agent=reservation.agent)

    def read_artifact(
        self, uri: str, *, offset: int = 0, limit: int | None = None
    ) -> dict[str, object]:
        return ArtifactResolver(self.artifacts).read(uri, offset=offset, limit=limit)

    def compact(
        self,
        messages: list[dict[str, Any]],
        *,
        goal: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        compacted, saved = compact_messages(messages, keep_recent=4)
        if goal and compacted:
            compacted[0] = {
                **compacted[0],
                "content": str(compacted[0].get("content", ""))
                + f"\nActive goal: {goal[:1200]}",
            }
        return compacted, saved

    def summarize(self, tool_name: str, result: Any) -> str:
        summary = summarize_tool_result(
            tool_name,
            result,
            max_chars=6_000,
            artifact_store=self.artifacts,
        )
        return json.dumps(summary, ensure_ascii=False, default=str)

    def store_trace(self) -> dict[str, object]:
        payload = "\n".join(
            json.dumps(event.to_dict(), default=str) for event in self.trace.events
        )
        artifact = self.artifacts.put(payload, "application/x-ndjson")
        return {
            "uri": artifact.uri,
            "sha256": artifact.digest,
            "events": len(self.trace.events),
        }

    def delta(self, state) -> dict[str, Any]:
        current = {
            "history": list(getattr(state, "history", [])[-4:]),
            "plan": getattr(state, "plan", None),
            "active_requirement": getattr(state, "active_requirement", None),
            "changed_files": sorted(
                getattr(state, "successful_mutation_paths", set()) or set()
            ),
            "verification": getattr(state, "successful_verification", False),
        }
        packet = delta_context(
            stable={"task": getattr(state, "user_message", "")},
            previous=self.previous_state,
            current=current,
        )
        self.previous_state = current
        return packet
