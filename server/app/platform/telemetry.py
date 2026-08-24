"""OpenTelemetry tracing facade with JSONL fallback for Server."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from time import perf_counter, time
from typing import Any, Iterator
import uuid


class ServerTelemetry:
    def __init__(self, service: str = "jarvis-server") -> None:
        self.service = service
        configured = os.getenv("JARVIS_OTEL_JSONL")
        self.path = Path(configured) if configured else Path("/tmp/jarvis-server-telemetry.jsonl")
        self.tracer = None
        try:
            from opentelemetry import trace  # type: ignore

            self.tracer = trace.get_tracer(service)
        except Exception:
            self.tracer = None

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        record = {
            "service": self.service,
            "name": name,
            "trace_id": uuid.uuid4().hex,
            "span_id": uuid.uuid4().hex[:16],
            "started_at": time(),
            "attributes": attributes,
            "status": "ok",
        }
        started = perf_counter()
        cm = self.tracer.start_as_current_span(name) if self.tracer else None
        native = cm.__enter__() if cm else None
        if native is not None:
            for key, value in attributes.items():
                try:
                    native.set_attribute(key, value)
                except Exception:
                    pass
        try:
            yield record
        except BaseException as exc:
            record["status"] = "error"
            record["error"] = str(exc)[:4000]
            if native is not None:
                try:
                    native.record_exception(exc)
                except Exception:
                    pass
            raise
        finally:
            record["duration_ms"] = (perf_counter() - started) * 1000
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            if cm:
                cm.__exit__(None, None, None)


telemetry = ServerTelemetry()
