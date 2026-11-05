"""End to end: generated ground truth against SQL over the built lake, which checks every connector."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from lifelake import db
from lifelake.cli import SOURCES, cmd_refresh, cmd_scan
from lifelake.query import SafeLake
from lifelake.scan import scan_lake
from lifelake.synth import DEMO_TRAVEL, DEMO_TZ, PARAMS
from lifelake.synth.core import KINDS

REPO = Path(__file__).resolve().parent.parent

COUNTS = [
    # claude.ai
    ("ai_conversations", "SELECT count(*) FROM claude_ai_conversations"),
    ("ai_messages", "SELECT count(*) FROM claude_ai_messages"),
    ("ai_human_messages", "SELECT count(*) FROM claude_ai_messages WHERE role = 'human'"),
    ("ai_assistant_messages", "SELECT count(*) FROM claude_ai_messages WHERE role = 'assistant'"),
    ("ai_starred", "SELECT count(*) FROM claude_ai_conversations WHERE is_starred"),
    ("ai_messages_in_trip", "SELECT count(*) FROM claude_ai_messages WHERE tz = 'Europe/Lisbon'"),
    # Claude Code
    ("cc_sessions", "SELECT count(DISTINCT session_id) FROM claude_code_messages"),
    ("cc_messages", "SELECT count(*) FROM claude_code_messages"),
    ("cc_real_prompts", "SELECT count(*) FROM claude_code_messages WHERE is_real_prompt"),
    ("cc_assistant_messages", "SELECT count(*) FROM claude_code_messages WHERE type = 'assistant'"),
    ("cc_subagent_messages", "SELECT count(*) FROM claude_code_messages WHERE transcript_kind = 'subagent'"),
    ("cc_input_tokens", "SELECT sum(input_tokens) FROM claude_code_messages"),
    ("cc_output_tokens", "SELECT sum(output_tokens) FROM claude_code_messages"),
    ("cc_cache_read_tokens", "SELECT sum(cache_read_input_tokens) FROM claude_code_messages"),
    ("cc_cache_creation_tokens", "SELECT sum(cache_creation_input_tokens) FROM claude_code_messages"),
    ("cc_live_rows", "SELECT count(*) FROM claude_code_messages WHERE origin = 'live'"),
    ("cc_messages_in_trip", "SELECT count(*) FROM claude_code_messages WHERE tz = 'Europe/Lisbon'"),
    # Takeout
    ("takeout_search", "SELECT count(*) FROM events WHERE source = 'google.activity.search'"),
    ("takeout_youtube", "SELECT count(*) FROM events WHERE source = 'google.activity.youtube'"),
    ("takeout_maps", "SELECT count(*) FROM events WHERE source = 'google.activity.maps'"),
    ("takeout_chrome", "SELECT count(*) FROM events WHERE source = 'chrome_sync.visit'"),
    ("takeout_location", "SELECT count(*) FROM events WHERE source = 'google.location'"),
    ("takeout_calendar", "SELECT count(*) FROM events WHERE source = 'calendar.event'"),
    ("takeout_email", "SELECT count(*) FROM events WHERE source = 'email'"),
    ("takeout_email_sent", "SELECT count(*) FROM events WHERE source = 'email' AND actor = 'me'"),
    # Spotify, raw events
    ("spotify_searches", "SELECT count(*) FROM events WHERE source = 'spotify.search'"),
    ("spotify_playlists", "SELECT count(*) FROM events WHERE source = 'spotify.playlist'"),
    ("spotify_inferences", "SELECT count(*) FROM events WHERE source = 'spotify.inference'"),
    ("spotify_library", "SELECT count(*) FROM events WHERE source = 'spotify.other.yourlibrary'"),
    # Both event connectors
    ("events_total", "SELECT count(*) FROM events"),
]

SAFE_COUNTS = [
    ("spotify_plays", "SELECT count(*) FROM safe.spotify_plays"),
    ("spotify_ms_played", "SELECT sum(ms_played) FROM safe.spotify_plays"),
    ("spotify_skipped", "SELECT count(*) FROM safe.spotify_plays WHERE skipped"),
    ("spotify_nameless_plays", "SELECT count(*) FROM safe.spotify_plays WHERE track IS NULL"),
    ("spotify_plays_in_trip", "SELECT count(*) FROM safe.spotify_plays WHERE tz = 'Europe/Lisbon'"),
    ("cc_real_prompts", "SELECT count(*) FROM safe.claude_code_messages WHERE is_real_prompt"),
    ("takeout_chrome", "SELECT count(*) FROM safe.web_visits"),
    ("takeout_location", "SELECT count(*) FROM safe.location_points"),
    ("takeout_calendar", "SELECT count(*) FROM safe.calendar_events"),
    ("takeout_email", "SELECT count(*) FROM safe.email_meta"),
]

GROUPS = [
    ("ai_models", "SELECT model, count(*) FROM claude_ai_messages WHERE role = 'assistant' GROUP BY 1"),
    ("cc_models", "SELECT model, count(*) FROM claude_code_messages WHERE type = 'assistant' GROUP BY 1"),
    ("cc_sessions_by_project", "SELECT project_key, count(DISTINCT session_id) FROM claude_code_messages GROUP BY 1"),
    ("cc_tools", "SELECT t, count(*) FROM (SELECT unnest(string_split(tool_names, ',')) AS t "
                 "FROM claude_code_messages WHERE tool_names IS NOT NULL) GROUP BY 1"),
    ("takeout_chrome_domains", "SELECT domain, count(*) FROM events WHERE source = 'chrome_sync.visit' GROUP BY 1"),
    ("takeout_email_sender_domains", "SELECT json_extract_string(payload, '$.sender_domain'), count(*) FROM events "
                                     "WHERE source = 'email' GROUP BY 1"),
]

RANGES = [
    ("ai_ts_range", "claude_ai_messages", "TRUE"),
    ("cc_ts_range", "claude_code_messages", "TRUE"),
    ("spotify_ts_range", "events", "source = 'spotify.play'"),
    ("takeout_search_ts_range", "events", "source = 'google.activity.search'"),
    ("takeout_youtube_ts_range", "events", "source = 'google.activity.youtube'"),
    ("takeout_maps_ts_range", "events", "source = 'google.activity.maps'"),
    ("takeout_chrome_ts_range", "events", "source = 'chrome_sync.visit'"),
    ("takeout_location_ts_range", "events", "source = 'google.location'"),
    ("takeout_calendar_ts_range", "events", "source = 'calendar.event'"),
    ("takeout_email_ts_range", "events", "source = 'email'"),
]


# --- ground truth against SQL ----------------------------------------------------------------

@pytest.mark.parametrize("key,sql", COUNTS, ids=[c[0] for c in COUNTS])
def test_count_matches_ground_truth(demo, key, sql):
    assert demo.one(sql) == demo.truth[key]


@pytest.mark.parametrize("key,sql", SAFE_COUNTS, ids=[c[0] for c in SAFE_COUNTS])
def test_safe_view_matches_ground_truth(demo, key, sql):
    with SafeLake(demo.db) as lake:
        assert lake.run_sql(sql)[1][0][0] == demo.truth[key]


@pytest.mark.parametrize("key,sql", GROUPS, ids=[g[0] for g in GROUPS])
def test_group_matches_ground_truth(demo, key, sql):
    assert dict(demo.rows(sql)) == demo.truth[key]


@pytest.mark.parametrize("key,table,where", RANGES, ids=[r[0] for r in RANGES])
def test_time_range_matches_ground_truth(demo, key, table, where):
    first, last = demo.rows(f"SELECT min(ts_utc), max(ts_utc) FROM {table} WHERE {where}")[0]
    assert [first, last] == [datetime.fromisoformat(x) for x in demo.truth[key]]


def test_top_artist(demo):
    with SafeLake(demo.db) as lake:
        _, rows = lake.run_sql("SELECT artist, count(*) FROM safe.spotify_plays WHERE artist IS NOT NULL "
                               "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1")
    assert rows[0] == (demo.truth["spotify_top_artist"], demo.truth["spotify_top_artist_plays"])


def test_local_hours_match_ground_truth(demo):
    """The lake takes local time from tz_periods and the generator works it out alone, so each checks the other."""
    got = dict(demo.rows("SELECT hour(local_ts)::VARCHAR, count(*) FROM claude_code_messages "
                         "WHERE is_real_prompt GROUP BY 1"))
    assert got == demo.truth["cc_prompts_by_local_hour"]


def test_every_timed_row_is_in_2024_with_a_zone(demo):
    for table in ("claude_ai_messages", "claude_code_messages"):
        first, last, no_zone = demo.rows(f"SELECT min(ts_utc), max(ts_utc), count_if(tz IS NULL) FROM {table}")[0]
        assert first.year == 2024 and last.year == 2024 and no_zone == 0
    first, last = demo.rows("SELECT min(ts_utc), max(ts_utc) FROM events")[0]
    assert first.year == 2024 and last.year == 2024
    assert demo.one("SELECT count(*) FROM events WHERE ts_utc IS NOT NULL AND tz IS NULL") == 0


def test_zones_are_home_and_trip_only(demo):
    assert {r[0] for r in demo.rows("SELECT DISTINCT tz FROM events WHERE tz IS NOT NULL")} == {
        "America/Chicago", "Europe/Lisbon"}


def test_trip_periods_loaded(demo):
    assert demo.rows("SELECT tz, note FROM tz_periods WHERE note LIKE 'travel%'") == [
        ("Europe/Lisbon", "travel 2024-06-03..2024-06-16")]


def test_manifest_counts_every_input_file(demo):
    got = dict(demo.rows("SELECT source, count(*) FROM manifest GROUP BY 1"))
    assert got == {"claude_ai": 1, "claude_code": demo.truth["cc_live_files"] + 1,
                   "takeout": demo.truth["takeout_files"], "spotify": demo.truth["spotify_files"]}


def test_bookkeeping_records_are_not_stored(demo):
    assert demo.one("SELECT count(*) FROM claude_code_messages WHERE type IN ('mode', 'ai-title', 'last-prompt')") == 0


def test_claude_code_copies_in_the_all_export_are_dropped(demo):
    assert demo.truth["ai_code_copies_ignored"] > 0
    assert demo.one("SELECT count(*) FROM claude_ai_messages m JOIN claude_code_messages c "
                    "ON m.message_id = c.uuid") == 0


# --- idempotence -----------------------------------------------------------------------------

def test_second_refresh_adds_nothing(demo, tmp_path, monkeypatch):
    before = hashlib.sha256(demo.db.read_bytes()).hexdigest()
    copy = tmp_path / "copy.duckdb"
    shutil.copy(demo.db, copy)
    monkeypatch.chdir(REPO)  # a Makefile-built lake holds paths relative to the repo root
    out = cmd_refresh(replace(demo.cfg, db_path=copy), list(SOURCES), write_schema=False)
    assert out["rows_added"] == 0
    assert all(s.get("files_loaded", 0) == 0 for s in out["sources"]), out
    assert hashlib.sha256(demo.db.read_bytes()).hexdigest() == before


# --- redaction -------------------------------------------------------------------------------

def test_scan_reports_zero(demo, capsys):
    con = db.connect(demo.db, read_only=True)
    try:
        res = scan_lake(con, workers=2)
    finally:
        con.close()
    assert res["hits"] == 0, res
    assert res["values_scanned"] > 0
    assert cmd_scan(demo.cfg) == 0
    assert json.loads(capsys.readouterr().out)["hits"] == 0


def test_redaction_log_has_hits_from_every_source(demo):
    by_source = dict(demo.rows("SELECT source, sum(hits) FROM redaction_log GROUP BY 1"))
    assert set(by_source) == set(demo.truth["secrets_by_source"])
    for source, planted in demo.truth["secrets_by_source"].items():
        assert by_source[source] >= planted, source


def test_redaction_log_covers_each_planted_type(demo):
    by_type = dict(demo.rows("SELECT type, sum(hits) FROM redaction_log GROUP BY 1"))
    for kind, planted in demo.truth["secrets_by_type"].items():
        assert by_type.get(kind, 0) >= planted, kind


def test_secrets_were_planted_in_every_field_kind(demo):
    assert set(demo.truth["secrets_by_field"]) == set(PARAMS["secret_rates"])
    assert len(demo.truth["secrets_by_type"]) == len(KINDS)


def test_no_planted_secret_reaches_the_lake(demo):
    text = demo.lake_text()
    assert "[REDACTED:" in text
    assert [s for s in demo.secrets if s in text] == []


def test_markers_replace_secrets_in_rows(demo):
    assert demo.one("SELECT count(*) FROM claude_ai_messages WHERE text LIKE '%[REDACTED:%'") > 0
    assert demo.one("SELECT count(*) FROM claude_code_messages WHERE tool_input LIKE '%[REDACTED:%' "
                    "OR tool_result LIKE '%[REDACTED:%' OR text LIKE '%[REDACTED:%'") > 0
    assert demo.one("SELECT count(*) FROM events WHERE source = 'spotify.playlist' "
                    "AND source_ref LIKE '%[REDACTED:%'") > 0
    assert demo.one("SELECT count(*) FROM events WHERE source = 'chrome_sync.visit' "
                    "AND url LIKE '%[REDACTED:url_credentials]@%'") > 0


def test_payloads_stay_valid_json_after_redaction(demo):
    for (p,) in demo.rows("SELECT payload FROM events WHERE payload LIKE '%[REDACTED:%'"):
        json.loads(p)


# --- identity fields -------------------------------------------------------------------------

def test_userdata_is_absent(demo):
    text = demo.lake_text()
    assert [m for m in demo.truth["userdata_markers"] if m in text] == []
    assert demo.one("SELECT count(*) FROM manifest WHERE file ILIKE '%userdata%'") == 0
    assert demo.one("SELECT count(*) FROM events WHERE src_file ILIKE '%userdata%'") == 0


def test_extended_history_identity_fields_are_absent(demo):
    text = demo.lake_text()
    assert [m for m in demo.truth["extended_markers"] if m in text] == []


def test_unrecognized_takeout_file_is_ignored(demo):
    assert demo.one("SELECT count(*) FROM manifest WHERE file ILIKE '%archive_browser%'") == 0


# --- Makefile --------------------------------------------------------------------------------

def test_makefile_matches_generator_settings():
    mk = (REPO / "Makefile").read_text()
    assert f"LIFELAKE_DEFAULT_TZ={DEMO_TZ}" in mk
    assert f"LIFELAKE_TRAVEL={DEMO_TRAVEL}" in mk
    assert "PY ?= python3" in mk
    assert "CLAUDE_CODE_SESSION_ID=" in mk
    assert "--seed 7" in mk
    for line in mk.splitlines():
        assert not line.startswith("\tpython "), line
