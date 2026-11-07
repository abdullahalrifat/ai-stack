"""Terminal-client side of the ai-stack Runs protocol."""

from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = 1
MIN_SERVER_PROTOCOL_VERSION = 1
MAX_SERVER_PROTOCOL_VERSION = 1
EVENT_SCHEMA_VERSION = 1
PROTOCOL_HEADER = "X-AIStack-Protocol-Version"


class ProtocolError(RuntimeError):
    pass


def validate_capabilities(
    capabilities: dict[str, Any],
    *,
    required_features: tuple[str, ...] = (),
) -> None:
    protocol = capabilities.get("protocol") or {}
    try:
        server_current = int(protocol.get("current", capabilities.get("api_version")))
        server_min_cli = int(protocol.get("min_cli", server_current))
        server_max_cli = int(protocol.get("max_cli", server_current))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            "The agent server did not advertise a valid protocol version. "
            "Upgrade the server before using this CLI."
        ) from exc
    if not server_min_cli <= PROTOCOL_VERSION <= server_max_cli:
        raise ProtocolError(
            f"Incompatible agent protocol: CLI uses {PROTOCOL_VERSION}, "
            f"server accepts {server_min_cli}..{server_max_cli}. "
            "Upgrade the CLI or server so their protocol ranges overlap."
        )
    if not MIN_SERVER_PROTOCOL_VERSION <= server_current <= MAX_SERVER_PROTOCOL_VERSION:
        raise ProtocolError(
            f"Unsupported server protocol {server_current}; this CLI supports "
            f"{MIN_SERVER_PROTOCOL_VERSION}..{MAX_SERVER_PROTOCOL_VERSION}."
        )
    features = {str(item) for item in capabilities.get("features", [])}
    missing = sorted(set(required_features) - features)
    if missing:
        raise ProtocolError(
            "The agent server is missing required capabilities: "
            + ", ".join(missing)
            + ". Upgrade the server or choose a mode that does not require them."
        )


def validate_event(event: dict[str, Any]) -> None:
    # Version-less events are accepted from protocol-v1 servers during the
    # migration window. Explicit future versions fail safely.
    version = event.get("schema_version", EVENT_SCHEMA_VERSION)
    try:
        parsed = int(version)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Agent returned an invalid event schema version.") from exc
    if parsed != EVENT_SCHEMA_VERSION:
        raise ProtocolError(
            f"Unsupported event schema {parsed}; CLI supports "
            f"{EVENT_SCHEMA_VERSION}. Upgrade the CLI."
        )
