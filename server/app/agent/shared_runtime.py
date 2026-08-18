"""Server adapter for the shared token-efficient Jarvis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis_core import (
    MemoryArtifactStore,
    TokenBudget,
    TokenLedger,
    Usage,
    compact_messages,
    delta_context,
    summarize_tool_result,
)
from jarvis_core.tokens import estimate_tokens

from ..core.config import (
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
    artifacts: MemoryArtifactStore
    previous_state: dict[str, Any]

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
            artifacts=MemoryArtifactStore(),
            previous_state={},
        )
        state.shared_runtime = runtime
        return runtime

    def reserve_turn(
        self,
        role: str,
        messages: object,
        tools: object,
        max_output_tokens: int,
    ) -> None:
        self.ledger.reserve(
            role,
            estimate_tokens({"messages": messages, "tools": tools}),
            max_output_tokens,
        )

    def record_turn(
        self,
        role: str,
        model: str,
        messages: object,
        tools: object,
        output: object,
    ) -> None:
        self.ledger.record(
            Usage(
                agent=role,
                model=model,
                input_tokens=estimate_tokens(
                    {"messages": messages, "tools": tools}
                ),
                output_tokens=estimate_tokens(output),
            )
        )

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
        return __import__("json").dumps(summary, ensure_ascii=False, default=str)

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
