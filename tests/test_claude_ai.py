"""claude.ai export connector: load, redaction, incremental refresh, local time."""
from __future__ import annotations

import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lifelake.cli import cmd_refresh
from lifelake.config import Config

COLS = ["source", "conversation_id", "conversation_name", "message_id", "parent_id", "role",
        "created_at", "text", "extra_json"]
SECRET_VALUE = "hunter" + "2" * 6 + "Qz"


def row(source, conv, mid, parent, role, ts, text, extra=None, name="Trip planning"):
    return {"source": source, "conversation_id": conv, "conversation_name": name, "message_id": mid,
            "parent_id": parent, "role": role, "created_at": ts, "text": text,
            "extra_json": json.dumps(extra if extra is not None else {})}


def write_export(path, rows):
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in COLS])), path)


def base_rows():
    m1 = row("claude.ai", "c1", "m1", None, "user", "2024-05-01T14:00:00Z",
             "my pass" + "word=" + SECRET_VALUE + " please remember it",
             {"message_index": 0, "model": "model-a", "platform": "web", "is_starred": False,
              "conversation_created_at": "2024-05-01T13:59:00Z",
              "attachments": [{"file_name": "notes.txt", "extracted_content": "hello"}],
              "files": [{"file_name": "pic.png"}]})
    m2 = row("claude.ai", "c1", "m2", "m1", "assistant", "2024-05-01T14:00:05Z", "Noted.",
             {"message_index": 1, "model": "model-a"})
    m3 = row("claude.ai", "c2", "m3", None, "user", "2024-06-10T12:00:00Z", "Where to eat?",
             {"message_index": 0}, name="Lisbon")
    code = row("claude_code", "s1", "u1", None, "user", "2024-05-02T10:00:00Z", "ignored code row")
    return [m1, m2, m3, code, dict(m2)]  # last one duplicates m2


@pytest.fixture
def cfg(tmp_path):
    exp = tmp_path / "export"
    exp.mkdir()
    write_export(exp / "claude_all_transcripts_2024.parquet", base_rows())
    return Config(db_path=tmp_path / "lake.duckdb", claude_export_dir=exp, default_tz="America/Chicago",
                  travel=[("Europe/Lisbon", "2024-06-03", "2024-06-16")])


def q(cfg, sql):
    con = duckdb.connect(str(cfg.db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_rows_load(cfg):
    res = cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    assert res["sources"][0]["source"] == "claude_ai"
    assert q(cfg, "SELECT message_id FROM claude_ai_messages ORDER BY 1") == [("m1",), ("m2",), ("m3",)]
    assert q(cfg, "SELECT conversation_id FROM claude_ai_conversations ORDER BY 1") == [("c1",), ("c2",)]
    assert q(cfg, "SELECT count(*) FROM claude_ai_messages WHERE message_id = 'u1'") == [(0,)]
    assert q(cfg, "SELECT model, message_index, files FROM claude_ai_messages WHERE message_id='m1'") == [
        ("model-a", 0, "pic.png")]


def test_secret_redacted(cfg):
    res = cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    (text,), = q(cfg, "SELECT text FROM claude_ai_messages WHERE message_id='m1'")
    assert SECRET_VALUE not in text and "[REDACTED:password]" in text
    assert res["sources"][0]["redactions"].get("password", 0) >= 1


def test_second_refresh_adds_nothing(cfg):
    cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    r2 = cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    assert r2["rows_added"] == 0
    assert r2["sources"][0].get("files_loaded", 0) == 0


def test_changed_file_adds_only_new_rows(cfg):
    cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    new = row("claude.ai", "c1", "m4", "m2", "user", "2024-05-01T14:05:00Z", "one more", {"message_index": 2})
    write_export(cfg.claude_export_dir / "claude_all_transcripts_2024.parquet", base_rows() + [new])
    r = cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    assert r["sources"][0]["files_loaded"] == 1
    assert r["rows_added"] == 1
    assert q(cfg, "SELECT count(*) FROM claude_ai_messages") == [(4,)]
    assert q(cfg, "SELECT count(*) FROM claude_ai_conversations") == [(2,)]


def test_local_ts_uses_travel_period(cfg):
    cmd_refresh(cfg, ["claude_ai"], write_schema=False)
    rows = q(cfg, "SELECT message_id, tz, strftime(local_ts, '%Y-%m-%d %H:%M') FROM claude_ai_messages "
                  "WHERE message_id IN ('m1', 'm3') ORDER BY 1")
    assert rows == [("m1", "America/Chicago", "2024-05-01 09:00"),
                    ("m3", "Europe/Lisbon", "2024-06-10 13:00")]
