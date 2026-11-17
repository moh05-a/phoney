"""Subscription hub: runs a collector only while someone is subscribed, and fans samples out."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from app.database.models import iso, utcnow
from app.monitoring.collectors import Collector

log = logging.getLogger("phoney.hub")

HISTORY_SIZE = 60


class Subscriber:
    """A connected client. Messages go through a bounded queue so one slow phone cannot stall the rest."""

    def __init__(self, max_queue: int = 200) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._loop = asyncio.get_running_loop()

    def push(self, message: dict[str, Any]) -> None:
        """Safe to call from any thread: the queue is only ever touched on the client's own loop."""
        try:
            on_own_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_own_loop = False
        if on_own_loop:
            self._put(message)
        else:
            self._loop.call_soon_threadsafe(self._put, message)

    def _put(self, message: dict[str, Any]) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest: live data is only useful when fresh
        self.queue.put_nowait(message)


class UnsupportedTopic(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Hub:
    def __init__(self, collectors: list[Collector]) -> None:
        self._collectors: dict[str, Collector] = {}
        self._unsupported: dict[str, str] = {}
        for collector in collectors:
            reason = collector.unsupported_reason()
            if reason is None:
                self._collectors[collector.topic] = collector
            else:
                self._unsupported[collector.topic] = reason
        self._snapshots: dict[str, Callable[[], dict[str, Any]]] = {}
        self._subs: dict[str, set[Subscriber]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._history: dict[str, deque[dict[str, Any]]] = {}

    # ---- registration -------------------------------------------------

    def register_event_topic(self, topic: str, snapshot: Callable[[], dict[str, Any]] | None = None) -> None:
        """An event topic has no sampling loop. `snapshot` gives new subscribers the current state."""
        self._subs.setdefault(topic, set())
        if snapshot is not None:
            self._snapshots[topic] = snapshot

    @property
    def topics(self) -> list[str]:
        return sorted(set(self._collectors) | set(self._subs))

    @property
    def unsupported(self) -> dict[str, str]:
        return dict(self._unsupported)

    @property
    def running_topics(self) -> list[str]:
        return sorted(self._tasks)

    def has_subscribers(self, topic: str) -> bool:
        return bool(self._subs.get(topic))

    # ---- subscriptions ------------------------------------------------

    def subscribe(self, sub: Subscriber, topic: str) -> int | None:
        """Returns the sampling interval in ms, or None for event topics."""
        collector = self._collectors.get(topic)
        if collector is None:
            if topic in self._unsupported:
                raise UnsupportedTopic("unsupported")
            if topic not in self._subs:
                raise UnsupportedTopic("unknown_topic")
            self._subs[topic].add(sub)
            if topic in self._snapshots:
                sub.push(self._message("data", topic, self._snapshots[topic]()))
            return None

        self._subs.setdefault(topic, set()).add(sub)
        history = self._history.get(topic)
        if history:
            sub.push({"type": "data", "topic": topic, "history": True, "payload": list(history)})
        if topic not in self._tasks:
            self._tasks[topic] = asyncio.create_task(self._run(collector), name=f"collector:{topic}")
        return int(collector.interval_s * 1000)

    def unsubscribe(self, sub: Subscriber, topic: str) -> None:
        subs = self._subs.get(topic)
        if not subs:
            return
        subs.discard(sub)
        if not subs and topic in self._tasks:
            self._tasks.pop(topic).cancel()
            self._history.pop(topic, None)  # stale samples would be misleading on the next connect

    def remove(self, sub: Subscriber) -> None:
        for topic in list(self._subs):
            self.unsubscribe(sub, topic)

    # ---- publishing ---------------------------------------------------

    def publish(self, topic: str, payload: dict[str, Any], kind: str = "event") -> None:
        message = self._message(kind, topic, payload)
        for sub in self._subs.get(topic, ()):
            sub.push(message)

    async def sample_once(self, topic: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._collectors[topic].sample)

    async def snapshot(self) -> dict[str, Any]:
        topics = [t for t in self._collectors if t.startswith("system.")]
        results = await asyncio.gather(*(self.sample_once(t) for t in topics), return_exceptions=True)
        return {t: r for t, r in zip(topics, results, strict=True) if not isinstance(r, BaseException)}

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- internals ----------------------------------------------------

    @staticmethod
    def _message(kind: str, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"type": kind, "topic": topic, "ts": iso(utcnow()), "payload": payload}

    async def _run(self, collector: Collector) -> None:
        topic = collector.topic
        history = self._history.setdefault(topic, deque(maxlen=HISTORY_SIZE))
        log.info("collector started: %s", topic)
        try:
            while True:
                try:
                    payload = await asyncio.to_thread(collector.sample)
                except Exception:
                    log.exception("collector %s failed; retrying", topic)
                else:
                    history.append(payload)
                    self.publish(topic, payload, kind="data")
                await asyncio.sleep(collector.interval_s)
        finally:
            log.info("collector stopped: %s", topic)
