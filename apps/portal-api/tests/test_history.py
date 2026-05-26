"""C3 history layer tests — uses an isolated in-memory-style temp DB."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture: redirect _DB_PATH to a tmp file so tests never touch real data
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point history._DB_PATH at a fresh temp file for every test."""
    db_file = tmp_path / "test_history.db"
    import app.history as hist
    monkeypatch.setattr(hist, "_DB_PATH", db_file)
    yield db_file
    # cleanup handled by tmp_path fixture


import app.history as history  # noqa: E402 — after monkeypatch setup in module scope


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_init_db_idempotent():
    """Calling init_db() twice must not raise."""
    history.init_db()
    history.init_db()  # second call — tables already exist, should be no-op


def test_record_and_list_chat():
    """Insert 3 turns; list returns all 3 in correct (most-recent-first) order."""
    history.init_db()
    cid = "conv-abc"
    history.record_chat_turn(cid, "user", "hello")
    history.record_chat_turn(cid, "assistant", "hi there")
    history.record_chat_turn(cid, "user", "how are you?")

    msgs = history.list_chat_messages(cid)
    assert len(msgs) == 3
    # most-recent first
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "how are you?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "hello"


def test_new_conversation_id_minted():
    """record_chat_turn(None, ...) mints a UUID; subsequent calls with that ID append."""
    history.init_db()
    cid = history.record_chat_turn(None, "user", "first message")
    assert cid  # non-empty

    cid2 = history.record_chat_turn(cid, "assistant", "reply")
    assert cid2 == cid  # same conversation threaded

    msgs = history.list_chat_messages(cid)
    assert len(msgs) == 2


def test_conversations_list():
    """list_conversations returns one row per conversation with correct counts."""
    history.init_db()
    history.record_chat_turn("conv-1", "user", "msg a")
    history.record_chat_turn("conv-1", "assistant", "reply a")
    history.record_chat_turn("conv-2", "user", "msg b")

    convs = history.list_conversations()
    conv_ids = {c["conversation_id"] for c in convs}
    assert "conv-1" in conv_ids
    assert "conv-2" in conv_ids

    c1 = next(c for c in convs if c["conversation_id"] == "conv-1")
    assert c1["message_count"] == 2


def test_swarm_upsert():
    """Insert 'running', then upsert to 'completed'; list returns one row with state=completed."""
    history.init_db()
    rid = "run-xyz"
    history.record_swarm_run(rid, 5, "Kimi-K2.6", "img:latest", 1000, None, "running", None, None)

    runs = history.list_swarm_runs()
    assert len(runs) == 1
    assert runs[0]["state"] == "running"
    assert runs[0]["ended_at"] is None

    # upsert to completed
    history.record_swarm_run(
        rid, 5, "Kimi-K2.6", "img:latest", 1000, 1500, "completed",
        {"passes": 4, "total": 5}, [{"idx": 1, "status": "PASS"}],
    )

    runs = history.list_swarm_runs()
    assert len(runs) == 1  # still one row (upsert, not insert)
    assert runs[0]["state"] == "completed"
    assert runs[0]["ended_at"] == 1500
    assert runs[0]["summary"] == {"passes": 4, "total": 5}
    assert runs[0]["leaderboard"] == [{"idx": 1, "status": "PASS"}]


def test_sandbox_create_then_expire():
    """record creation → list shows expired_at=None; record_sandbox_expiry → row updated."""
    history.init_db()
    sid = "sb-0001"
    history.record_sandbox_creation(sid, "img:latest", "kata-vm-isolation", 2000)

    rows = history.list_sandbox_creations()
    assert len(rows) == 1
    assert rows[0]["sandbox_id"] == sid
    assert rows[0]["expired_at"] is None

    history.record_sandbox_expiry(sid, 3000, "manual")

    rows = history.list_sandbox_creations()
    assert rows[0]["expired_at"] == 3000
    assert rows[0]["expiry_reason"] == "manual"


def test_sandbox_expiry_noop_for_unknown():
    """record_sandbox_expiry on an unknown id must not raise."""
    history.init_db()
    history.record_sandbox_expiry("nonexistent-id", 9999, "auto-expire")  # no error


def test_sandbox_create_idempotent():
    """INSERT OR IGNORE — creating the same sandbox_id twice is a no-op."""
    history.init_db()
    sid = "sb-dupe"
    history.record_sandbox_creation(sid, "img:v1", "kata-vm-isolation", 1000)
    history.record_sandbox_creation(sid, "img:v2", "runc", 2000)  # should be ignored

    rows = history.list_sandbox_creations()
    assert len(rows) == 1
    assert rows[0]["image"] == "img:v1"  # first write preserved
