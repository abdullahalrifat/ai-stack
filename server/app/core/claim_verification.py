"""Server adapters for claim-level source verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jarvis_core import (
    ClaimAssessment,
    SourceAssessment,
    SourceKind,
    rank_sources,
)


def verify_claims(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for item in items:
        sources = []
        for source in item.get("sources", []):
            published = source.get("published_at")
            sources.append(
                SourceAssessment(
                    url=str(source["url"]),
                    title=str(source.get("title") or source["url"]),
                    kind=SourceKind(str(source.get("kind", "unknown"))),
                    published_at=datetime.fromisoformat(published) if published else None,
                    supports=bool(source.get("supports", True)),
                )
            )
        assessment = ClaimAssessment(str(item["claim"]), sources)
        results.append(
            {
                "claim": assessment.claim,
                "confidence": assessment.confidence(),
                "consensus": assessment.consensus(),
                "contradictions": assessment.contradictions,
                "independent_domains": assessment.independent_domains,
                "sources": [
                    {
                        "url": source.url,
                        "title": source.title,
                        "kind": source.kind.value,
                        "freshness": source.freshness(),
                        "supports": source.supports,
                    }
                    for source in rank_sources(sources)
                ],
            }
        )
    return results
