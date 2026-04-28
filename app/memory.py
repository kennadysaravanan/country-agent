"""
Per-session short-term memory.

A dead-simple `dict[session_id, list[BaseMessage]]` protected by a lock so
multiple FastAPI workers / threads don't trample each other.

Why not LangGraph's MemorySaver?
- The brief asked for "per session_id, multi-user safe, in-memory dict".
- This is more transparent: every operation is a 5-line method you can read.
- Easy swap to Redis later: implement the same `get` / `append` / `clear`
  interface against a Redis client and inject it.

History is capped at `settings.max_history_messages` (oldest dropped first)
so we never blow the context window or leak unbounded memory.
"""
from __future__ import annotations

import threading
from collections import defaultdict

from langchain_core.messages import BaseMessage

from app.config import settings


class SessionMemory:
    """Thread-safe in-memory store of message histories keyed by session_id."""

    def __init__(self, max_messages: int = settings.max_history_messages) -> None:
        self._store: dict[str, list[BaseMessage]] = defaultdict(list)
        self._lock = threading.Lock()
        self._max = max_messages

    def get(self, session_id: str) -> list[BaseMessage]:
        """Return a copy of the current history for `session_id` (never None)."""
        with self._lock:
            return list(self._store[session_id])

    def append(self, session_id: str, messages: list[BaseMessage]) -> None:
        """Append messages and trim to the configured cap."""
        if not messages:
            return
        with self._lock:
            history = self._store[session_id]
            history.extend(messages)
            # Keep only the most recent N messages.
            if len(history) > self._max:
                self._store[session_id] = history[-self._max :]

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def stats(self) -> dict:
        """Useful for /health debugging."""
        with self._lock:
            return {
                "sessions": len(self._store),
                "total_messages": sum(len(h) for h in self._store.values()),
            }


# Module-level singleton — one store for the whole process.
session_memory = SessionMemory()
