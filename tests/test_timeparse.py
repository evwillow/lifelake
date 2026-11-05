"""Timestamp parsing, domains, travel specs and the time zone periods."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from lifelake import db
from lifelake.config import parse_travel
from lifelake.timeparse import domain_of, from_unix, from_webkit, iso, parse_any

UTC = timezone.utc
T0 = datetime(2024, 5, 1, 13, 20, 0, tzinfo=UTC)
EPOCH_S = 1714569600


# --- parse_any -------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expect", [
    ("2024-05-01T13:20:00+00:00", T0),
    ("2024-05-01T08:20:00-05:00", T0),
    ("2024-05-01T13:20:00Z", T0),
    ("2024-05-01T13:20:00.000Z", T0),
    ("2024-05-01T13:20:00.123456789Z", T0.replace(microsecond=123456)),
    ("2024-05-01 13:20:00", T0),
    ("2024-05-01 13:20", T0),
    ("20240501T132000Z", T0),
    ("20240501T132000", T0),
    ("20240501", datetime(2024, 5, 1, tzinfo=UTC)),
    ("May 1, 2024, 1:20:00 PM", T0),
    (EPOCH_S, datetime.fromtimestamp(EPOCH_S, UTC)),
    (str(EPOCH_S), datetime.fromtimestamp(EPOCH_S, UTC)),
    (EPOCH_S * 1000, datetime.fromtimestamp(EPOCH_S, UTC)),
    (str(EPOCH_S * 1000), datetime.fromtimestamp(EPOCH_S, UTC)),
    (str(EPOCH_S * 1_000_000), datetime.fromtimestamp(EPOCH_S, UTC)),
    ("13359043200000000", datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=13359043200000000)),
    ("Wed, 01 May 2024 08:20:00 -0500", T0),
    ("01 May 2024 13:20:00 +0000", T0),
    ("Wed, 1 May 2024 13:20:00 GMT", T0),
])
def test_parse_any(value, expect):
    got = parse_any(value)
    assert got == expect
    assert got.tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", "13/45/2024"])
def test_parse_any_rejects(value):
    assert parse_any(value) is None


def test_webkit_value_is_in_2024():
    assert parse_any("13359043200000000").year == 2024


def test_from_unix_units_and_empty():
    assert from_unix(EPOCH_S) == from_unix(EPOCH_S * 1000, "ms") == from_unix(EPOCH_S * 1_000_000, "us")
    assert from_unix(None) is None and from_unix(0) is None and from_unix("") is None
    assert from_unix(1e30) is None


def test_from_webkit():
    assert from_webkit(11644473600000000) == datetime(1970, 1, 1, tzinfo=UTC)
    assert from_webkit(None) is None and from_webkit(0) is None


def test_iso():
    assert iso(T0) == "2024-05-01T13:20:00.000000Z"
    assert iso(datetime(2024, 5, 1, 13, 20)) == "2024-05-01T13:20:00.000000Z"
    assert iso(None) is None
    offset = timezone(timedelta(hours=-5))
    assert iso(datetime(2024, 5, 1, 8, 20, tzinfo=offset)) == "2024-05-01T13:20:00.000000Z"


# --- domain_of -------------------------------------------------------------------------------

@pytest.mark.parametrize("url,expect", [
    ("https://www.example.com/a?b=1", "example.com"),
    ("https://example.org", "example.org"),
    ("HTTP://WWW.Example.COM/x", "example.com"),
    ("https://docs.example.com:8443/x", "docs.example.com"),
    ("http://en.wikipedia.org#top", "en.wikipedia.org"),
    ("ftp://files.example.net/a", "files.example.net"),
    ("mailto:someone@example.org", "example.org"),
    ("mailto:example.org", "example.org"),
    ("https://admin:[REDACTED:url_credentials]@db.example.com/x", "db.example.com"),
    ("https://user@example.net/x", "example.net"),
    ("not a url", None),
    ("/relative/path", None),
    ("", None),
    (None, None),
])
def test_domain_of(url, expect):
    assert domain_of(url) == expect


# --- parse_travel ----------------------------------------------------------------------------

def test_parse_travel_empty():
    assert parse_travel(None) == [] and parse_travel("") == [] and parse_travel(" , ") == []


def test_parse_travel_one():
    assert parse_travel("Europe/Lisbon:2024-06-03..2024-06-16") == [("Europe/Lisbon", "2024-06-03", "2024-06-16")]


def test_parse_travel_sorts_by_start():
    got = parse_travel("Asia/Tokyo:2024-09-01..2024-09-05, Europe/Lisbon:2024-06-03..2024-06-16")
    assert [t[0] for t in got] == ["Europe/Lisbon", "Asia/Tokyo"]


@pytest.mark.parametrize("bad", ["Europe/Lisbon", "Europe/Lisbon:2024-06-03", ":2024-06-03..2024-06-16",
                                 "Europe/Lisbon:..2024-06-16", "Europe/Lisbon:2024-06-03.."])
def test_parse_travel_bad(bad):
    with pytest.raises(ValueError):
        parse_travel(bad)


# --- tz periods ------------------------------------------------------------------------------

TRAVEL = [("Europe/Lisbon", "2024-06-03", "2024-06-16"), ("Asia/Tokyo", "2024-09-01", "2024-09-05")]


def assert_contiguous(rows):
    assert rows[0][1] == "2000-01-01" and rows[-1][2] == "2100-01-01"
    for prev, cur in zip(rows, rows[1:], strict=False):
        assert prev[2] == cur[1], f"gap between {prev} and {cur}"
    for _tz, a, b, _note in rows:
        assert date.fromisoformat(a) <= date.fromisoformat(b)


@pytest.mark.parametrize("travel", [(), TRAVEL[:1], TRAVEL])
def test_tz_period_rows_have_no_gaps(travel):
    assert_contiguous(db.tz_period_rows("America/Chicago", travel))


def test_tz_period_rows_zones_and_end_exclusive():
    rows = db.tz_period_rows("America/Chicago", TRAVEL[:1])
    assert [r[0] for r in rows] == ["America/Chicago", "Europe/Lisbon", "America/Chicago"]
    assert rows[1][1:3] == ("2024-06-03", "2024-06-17")  # last travel day is inclusive, so the end is the day after
    assert rows[1][3] == "travel 2024-06-03..2024-06-16"


def test_no_travel_is_one_default_period():
    assert db.tz_period_rows("UTC", ()) == [("UTC", "2000-01-01", "2100-01-01", "default")]


@pytest.mark.parametrize("ts,zone,local", [
    # Chicago in winter and in summer
    ("2024-01-15 18:00:00+00", "America/Chicago", "2024-01-15 12:00:00"),
    ("2024-05-01 14:00:00+00", "America/Chicago", "2024-05-01 09:00:00"),
    # first and last travel days, and the instants either side of the UTC bounds
    ("2024-06-02 23:59:59+00", "America/Chicago", "2024-06-02 18:59:59"),
    ("2024-06-03 00:00:00+00", "Europe/Lisbon", "2024-06-03 01:00:00"),
    ("2024-06-10 12:00:00+00", "Europe/Lisbon", "2024-06-10 13:00:00"),
    ("2024-06-16 23:59:59+00", "Europe/Lisbon", "2024-06-17 00:59:59"),
    ("2024-06-17 00:00:00+00", "America/Chicago", "2024-06-16 19:00:00"),
])
def test_travel_maps_to_zone_and_local_time(tmp_path, ts, zone, local):
    con = db.connect(tmp_path / "t.duckdb", default_tz="America/Chicago", travel=TRAVEL[:1])
    try:
        got = con.execute(f"""
            SELECT p.tz, strftime(timezone(p.tz, s.ts), '%Y-%m-%d %H:%M:%S')
            FROM (SELECT ?::TIMESTAMPTZ AS ts) s {db.local_join('s', 'ts')}
        """, [ts]).fetchone()
    finally:
        con.close()
    assert got == (zone, local)


def test_periods_cover_every_instant_exactly_once(tmp_path):
    con = db.connect(tmp_path / "t.duckdb", default_tz="America/Chicago", travel=TRAVEL)
    try:
        for ts in ("2000-01-01 00:00:00+00", "2024-06-03 00:00:00+00", "2024-06-17 00:00:00+00",
                   "2024-09-01 00:00:00+00", "2024-09-05 23:59:59+00", "2099-12-31 23:59:59+00"):
            n = con.execute(f"SELECT count(*) FROM (SELECT ?::TIMESTAMPTZ AS ts) s {db.local_join('s', 'ts')} "
                            "WHERE p.tz IS NOT NULL", [ts]).fetchone()[0]
            assert n == 1, ts
    finally:
        con.close()


def test_periods_written_once(tmp_path):
    path = tmp_path / "t.duckdb"
    db.connect(path, default_tz="America/Chicago", travel=TRAVEL).close()
    con = db.connect(path, default_tz="UTC")
    try:
        assert con.execute("SELECT count(*) FROM tz_periods").fetchone()[0] == 5
        assert con.execute("SELECT DISTINCT tz FROM tz_periods WHERE note = 'default'").fetchall() == [
            ("America/Chicago",)]
    finally:
        con.close()
