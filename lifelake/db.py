"""DuckDB connection, DDL, time zones, manifest and run bookkeeping."""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pyarrow as pa

Z = "USING COMPRESSION zstd"  # text columns; needs storage_compatibility_version >= v1.2

DDL = f"""
CREATE TABLE IF NOT EXISTS manifest (
  source VARCHAR, file VARCHAR, sha256 VARCHAR, rows BIGINT, watermark VARCHAR, ts TIMESTAMPTZ,
  PRIMARY KEY (source, file));
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR, source VARCHAR, started TIMESTAMPTZ, seconds DOUBLE, files_seen BIGINT,
  files_loaded BIGINT, files_skipped BIGINT, rows_extracted BIGINT, rows_added BIGINT,
  status VARCHAR, note VARCHAR);
CREATE TABLE IF NOT EXISTS redaction_log (run_id VARCHAR, source VARCHAR, type VARCHAR, hits BIGINT);
CREATE TABLE IF NOT EXISTS tz_periods (tz VARCHAR, start_utc TIMESTAMPTZ, end_utc TIMESTAMPTZ, note VARCHAR);

CREATE TABLE IF NOT EXISTS claude_ai_conversations (
  conversation_id VARCHAR PRIMARY KEY, name VARCHAR {Z}, created_utc TIMESTAMPTZ, updated_utc TIMESTAMPTZ,
  local_created_ts TIMESTAMP, tz VARCHAR, platform VARCHAR, is_starred BOOLEAN, summary VARCHAR {Z},
  src_file VARCHAR);
CREATE TABLE IF NOT EXISTS claude_ai_messages (
  message_id VARCHAR, conversation_id VARCHAR, parent_id VARCHAR, role VARCHAR, message_index INTEGER,
  ts_utc TIMESTAMPTZ, local_ts TIMESTAMP, tz VARCHAR, model VARCHAR, truncated BOOLEAN,
  text VARCHAR {Z}, attachments VARCHAR {Z}, files VARCHAR {Z}, src_file VARCHAR);

CREATE TABLE IF NOT EXISTS claude_code_messages (
  session_id VARCHAR, uuid VARCHAR, parent_uuid VARCHAR, root_session_id VARCHAR, agent_id VARCHAR,
  workflow_id VARCHAR, transcript_kind VARCHAR, project_key VARCHAR, rel_path VARCHAR, line_no BIGINT,
  type VARCHAR, subtype VARCHAR, role VARCHAR, ts_utc TIMESTAMPTZ, local_ts TIMESTAMP, tz VARCHAR,
  cwd VARCHAR, git_branch VARCHAR, cli_version VARCHAR, model VARCHAR, entrypoint VARCHAR,
  user_type VARCHAR, is_sidechain BOOLEAN, is_meta BOOLEAN, prompt_source VARCHAR, origin_kind VARCHAR,
  prompt_id VARCHAR, request_id VARCHAR, is_real_prompt BOOLEAN, tool_names VARCHAR,
  text VARCHAR {Z}, thinking VARCHAR {Z}, tool_input VARCHAR {Z}, tool_result VARCHAR {Z},
  input_tokens BIGINT, output_tokens BIGINT, cache_read_input_tokens BIGINT,
  cache_creation_input_tokens BIGINT, stop_reason VARCHAR, origin VARCHAR, src_file VARCHAR);

-- Takeout, Spotify and any other event-shaped connector. payload is JSON with string leaves redacted.
CREATE TABLE IF NOT EXISTS events (
  event_id VARCHAR PRIMARY KEY, source VARCHAR, source_ref VARCHAR, ts_utc TIMESTAMPTZ, local_ts TIMESTAMP,
  tz VARCHAR, device VARCHAR, actor VARCHAR, context VARCHAR {Z}, text VARCHAR {Z}, url VARCHAR {Z},
  domain VARCHAR, payload VARCHAR {Z}, src_file VARCHAR);
"""

def tz_period_rows(default_tz: str = "UTC", travel=()) -> list[tuple[str, str, str, str]]:
    """Contiguous periods covering 2000..2100. Bounds are UTC midnights; end is exclusive.

    ``travel`` is [(zone, first UTC date, last UTC date inclusive)], sorted and not overlapping.
    Everything outside it uses ``default_tz``.
    """
    rows, cur = [], "2000-01-01"
    for zone, a, b in travel:
        end_s = (date.fromisoformat(b) + timedelta(days=1)).isoformat()
        rows.append((default_tz, cur, a, "default"))
        rows.append((zone, a, end_s, f"travel {a}..{b}"))
        cur = end_s
    rows.append((default_tz, cur, "2100-01-01", "default"))
    return rows


def connect(path: Path | str, read_only: bool = False, default_tz: str = "UTC", travel=()) -> duckdb.DuckDBPyConnection:
    cfg = {} if read_only else {"storage_compatibility_version": "latest"}
    con = duckdb.connect(str(path), read_only=read_only, config=cfg)
    con.execute("SET TimeZone = 'UTC'")
    if not read_only:
        con.execute(DDL)
        if con.execute("SELECT count(*) FROM tz_periods").fetchone()[0] == 0:
            con.executemany("INSERT INTO tz_periods VALUES (?, ?::TIMESTAMPTZ, ?::TIMESTAMPTZ, ?)",
                            [(tz, a + " 00:00:00+00", b + " 00:00:00+00", n)
                             for tz, a, b, n in tz_period_rows(default_tz, travel)])
    return con


def local_join(alias: str, ts_col: str) -> str:
    """SQL fragment: LEFT JOIN tz_periods for alias.ts_col (use p.tz and timezone(p.tz, ...))."""
    return f"LEFT JOIN tz_periods p ON {alias}.{ts_col} >= p.start_utc AND {alias}.{ts_col} < p.end_utc"


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def manifest_get(con, source: str) -> dict[str, tuple[str, str]]:
    return {f: (sha, wm) for f, sha, wm in
            con.execute("SELECT file, sha256, watermark FROM manifest WHERE source = ?", [source]).fetchall()}


def manifest_put(con, source: str, file: str, sha: str, rows: int, watermark: str) -> None:
    con.execute("INSERT OR REPLACE INTO manifest VALUES (?, ?, ?, ?, ?, ?)",
                [source, file, sha, rows, watermark, datetime.now(timezone.utc)])


def insert_staged(con, table: str, rows: list[dict], schema: pa.Schema, key: list[str],
                  select: str | None = None, order: str = "1", ts_col: str = "ts_utc") -> int:
    """Insert rows whose key is not already in ``table``; returns rows added.

    ``select`` is the column list to insert (defaults to the schema's columns, plus local
    time columns filled from tz_periods when the table has them).
    """
    if not rows:
        return 0
    tbl = pa.Table.from_pylist(rows, schema=schema)
    con.register("staged_rows", tbl)
    try:
        keys = " AND ".join(f"t.{k} IS NOT DISTINCT FROM s.{k}" for k in key)
        part = ", ".join(key)
        n = con.execute(f"""
            INSERT INTO {table} BY NAME
            SELECT {select or 's.*'} FROM (
              SELECT * FROM staged_rows QUALIFY row_number() OVER (PARTITION BY {part} ORDER BY {order}) = 1
            ) s
            {local_join('s', ts_col) if select and 'p.tz' in select else ''}
            WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {keys})
        """).fetchone()[0]
    finally:
        con.unregister("staged_rows")
    return int(n)


def log_run(con, run_id: str, source: str, started: datetime, seconds: float, stats: dict,
            counts: Counter, status: str = "ok", note: str = "") -> None:
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [run_id, source, started, seconds, stats.get("files_seen", 0), stats.get("files_loaded", 0),
                 stats.get("files_skipped", 0), stats.get("rows_extracted", 0), stats.get("rows_added", 0),
                 status, note])
    if counts:
        con.executemany("INSERT INTO redaction_log VALUES (?,?,?,?)",
                        [(run_id, source, t, n) for t, n in sorted(counts.items())])
