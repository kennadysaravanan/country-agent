"""Tests for the per-session memory store."""
from __future__ import annotations

import os
import threading

os.environ.setdefault("GROQ_API_KEY", "dummy")

from langchain_core.messages import AIMessage, HumanMessage

from app.memory import SessionMemory


def test_isolation_between_sessions():
    mem = SessionMemory(max_messages=10)
    mem.append("alice", [HumanMessage(content="hi"), AIMessage(content="hello alice")])
    mem.append("bob", [HumanMessage(content="hey")])
    assert len(mem.get("alice")) == 2
    assert len(mem.get("bob")) == 1
    assert mem.get("alice")[0].content == "hi"


def test_history_is_capped():
    mem = SessionMemory(max_messages=3)
    for i in range(10):
        mem.append("u", [HumanMessage(content=f"m{i}")])
    history = mem.get("u")
    assert len(history) == 3
    assert [m.content for m in history] == ["m7", "m8", "m9"]


def test_clear_only_targets_one_session():
    mem = SessionMemory()
    mem.append("a", [HumanMessage(content="x")])
    mem.append("b", [HumanMessage(content="y")])
    mem.clear("a")
    assert mem.get("a") == []
    assert len(mem.get("b")) == 1


def test_thread_safety():
    """Hammer the store from multiple threads — no exceptions, no lost writes."""
    mem = SessionMemory(max_messages=10000)

    def worker(uid: str, n: int):
        for i in range(n):
            mem.append(uid, [HumanMessage(content=f"{uid}-{i}")])

    threads = [
        threading.Thread(target=worker, args=(f"u{i}", 50)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(8):
        assert len(mem.get(f"u{i}")) == 50


def test_get_returns_copy_not_reference():
    """Caller must not be able to mutate internal state."""
    mem = SessionMemory()
    mem.append("u", [HumanMessage(content="a")])
    history = mem.get("u")
    history.append(HumanMessage(content="injected"))
    assert len(mem.get("u")) == 1  # internal store is unchanged
