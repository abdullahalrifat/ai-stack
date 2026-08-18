"""Low-latency publication for durable run events.

PostgreSQL is the source of truth and supports reconnect/replay. Redis Pub/Sub
only wakes connected clients immediately; losing a Pub/Sub message is safe
because clients resume from the durable event id.
"""

import json
from functools import lru_cache
from typing import Any

import redis

from ..core.config import REDIS_URL


def channel_for(run_id: str) -> str:
    return f"agent-run:{run_id}"


class RunEventPublisher:
    def __init__(self, url: str):
        self.client = redis.Redis.from_url(url, decode_responses=True)

    def publish(self, event: dict[str, Any]) -> None:
        self.client.publish(
            channel_for(str(event["run_id"])), json.dumps(event, default=str)
        )

    def subscribe(self, run_id: str):
        subscription = self.client.pubsub(ignore_subscribe_messages=True)
        subscription.subscribe(channel_for(run_id))
        return subscription


@lru_cache(maxsize=1)
def get_event_publisher() -> RunEventPublisher:
    return RunEventPublisher(REDIS_URL)
