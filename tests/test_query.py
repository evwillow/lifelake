"""The read-only guard and the safe views, over the demo lake."""
from __future__ import annotations

import re

import pytest

from lifelake.query import Rejected, SafeLake, safe_views

# Columns that hold free text in the raw tables. None of these may be readable through a safe view.
RAW_TEXT_COLUMNS = {"text", "thinking", "tool_input", "tool_result", "attachments", "files", "name", "summary",
                    "context", "url", "payload", "cwd", "source_ref"}


@pytest.fixture(scope="module")
def lake(demo):
    with SafeLake(demo.db) as safe:
        yield safe


@pytest.mark.parametrize("sql", [
    "DELETE FROM safe.runs",
    "DELETE FROM lake.main.runs",
    "DROP TABLE lake.main.events",
    "DROP VIEW safe.events",
    "UPDATE safe.runs SET status = 'x'",
    "INSERT INTO safe.runs SELECT * FROM safe.runs",
    "CREATE TABLE t AS SELECT 1",
    "ATTACH 'other.duckdb' AS other",
    "DETACH lake",
    "COPY lake.main.events TO 'out.csv'",
    "COPY (SELECT 1) TO 'out.csv'",
    "SET enable_external_access = true",
    "INSTALL httpfs",
    "PRAGMA database_list",
])
def test_non_select_rejected(lake, sql):
    with pytest.raises(Rejected):
        lake.validate(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM lake.main.claude_code_messages",
    "SELECT text FROM lake.main.claude_ai_messages",
    "SELECT payload FROM lake.main.events LIMIT 1",
    "SELECT * FROM lake.main.manifest",
    "SELECT * FROM main.events",
    "SELECT * FROM claude_code_messages_raw",
    "SELECT s.session_id FROM safe.claude_code_messages s JOIN lake.main.events e ON TRUE",
    "WITH x AS (SELECT * FROM lake.main.events) SELECT * FROM x",
    "SELECT * FROM (SELECT text FROM lake.main.claude_ai_messages) t",
    "SELECT (SELECT max(text) FROM lake.main.claude_ai_messages)",
    "SELECT 1 UNION ALL SELECT count(*) FROM lake.main.events",
])
def test_raw_tables_rejected(lake, sql):
    with pytest.raises(Rejected):
        lake.validate(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM duckdb_columns()",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM duckdb_tables",
    "SELECT * FROM read_csv('data.csv')",
    "SELECT * FROM read_csv_auto('data.csv')",
    "SELECT * FROM read_parquet('data.parquet')",
    "SELECT * FROM read_text('notes.txt')",
    "SELECT * FROM 'data.csv'",
    "SELECT * FROM 'data.parquet'",
    "FROM glob('*')",
])
def test_system_tables_and_functions_rejected(lake, sql):
    with pytest.raises(Rejected):
        lake.validate(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE lake.main.events",
    "SELECT count(*) FROM safe.events; DELETE FROM safe.runs;",
])
def test_multiple_statements_rejected(lake, sql):
    with pytest.raises(Rejected, match="exactly one"):
        lake.validate(sql)


@pytest.mark.parametrize("sql", ["", "   ", ";", "SELEC 1", "not sql at all"])
def test_empty_and_garbage_rejected(lake, sql):
    with pytest.raises(Rejected):
        lake.validate(sql)


def test_writes_fail_even_past_the_guard(lake):
    """The attachment itself is read-only, so a direct write fails at the database too."""
    with pytest.raises(Exception, match="(?i)read.only"):
        lake.con.execute("DELETE FROM lake.main.runs")


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM safe.events",
    "select count(*) from events",
    "  SELECT 1 ;  ",
    "WITH x AS (SELECT source, count(*) AS n FROM safe.events GROUP BY 1) SELECT * FROM x ORDER BY n DESC",
    "SELECT tz, count(*) FROM safe.claude_code_messages GROUP BY tz",
    "(SELECT 1)",
    "FROM safe.runs",
])
def test_plain_selects_pass(lake, sql):
    assert lake.validate(sql)
    cols, _rows = lake.run_sql(sql)
    assert cols


def test_views_listed():
    assert {"claude_ai_conversations", "claude_ai_messages", "claude_code_messages", "events", "spotify_plays",
            "google_activity", "web_visits", "location_points", "calendar_events", "email_meta", "manifest",
            "redaction_log", "runs", "tz_periods"} == set(safe_views())


@pytest.mark.parametrize("view", safe_views())
def test_select_over_each_safe_view_works(lake, view):
    cols, rows = lake.run_sql(f"SELECT * FROM safe.{view} LIMIT 5")
    assert cols and rows, view


@pytest.mark.parametrize("view", safe_views())
def test_safe_views_expose_no_text_column(lake, view):
    cols = {r[0] for r in lake.con.execute(f"DESCRIBE safe.{view}").fetchall()}
    assert not cols & RAW_TEXT_COLUMNS, view


def test_location_view_has_no_coordinates(lake):
    cols = {r[0] for r in lake.con.execute("DESCRIBE safe.location_points").fetchall()}
    assert cols == {"ts_utc", "local_ts", "tz"}


def test_context_has_no_raw_text_columns(lake):
    ctx = lake.context()
    listed = set(re.findall(r"^- (\w+) ", ctx, re.M))
    assert listed and not listed & RAW_TEXT_COLUMNS
    assert "text_chars" in listed
    for view in safe_views():
        assert f"safe.{view}" in ctx


def test_context_shows_no_secret_and_no_marker(lake, demo):
    ctx = lake.context()
    assert "[REDACTED:" not in ctx
    assert [s for s in demo.secrets if s in ctx] == []


def test_safe_view_text_columns_hold_no_body_text(lake):
    """text_chars is a length, not the text."""
    _, rows = lake.run_sql("SELECT max(text_chars) FROM safe.claude_ai_messages")
    assert isinstance(rows[0][0], int) and rows[0][0] > 0
