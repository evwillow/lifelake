import json
import shutil
from datetime import datetime, timezone

import pytest

from lifelake import cli, db
from lifelake.config import Config
from lifelake.sources import takeout as tk

FAKE = "ghp_" + "Zq9" * 12  # runtime-built fake token

HTML = (
    '<html><body>'
    '<div class="outer-cell mdl-cell"><div class="header-cell"><p class="mdl-typography--title">Maps</p></div>'
    '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Searched for '
    '<a href="https://example.com/place?q=1&amp;x=2">cafe</a><br>Mar 5, 2024, 3:04:05 PM PST</div>'
    '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">Locations: Springfield '
    '<a href="https://www.google.com/maps/@?center=12.345678,-98.765432">here</a></div></div>'
    '<div class="outer-cell mdl-cell"><div class="header-cell"><p class="mdl-typography--title">Maps</p></div>'
    '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Viewed area'
    '<br>Mar 6, 2024, 9:00:00 AM UTC</div></div>'
    '</body></html>')

ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:ev-1@example.com\r\nSUMMARY:Planning call\r\n"
    "DTSTART;TZID=America/New_York:20240710T090000\r\nCREATED:20240701T000000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:ev-2@example.com\r\nSUMMARY:Offsite\r\nDTSTART:20240801T120000Z\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n")

MBOX = (
    "From a@example.com Mon Jan  1 00:00:00 2024\n"
    "From: Alice <alice@example.org>\nTo: bob@example.com\nSubject: Hello\n"
    "Date: Tue, 02 Jan 2024 10:00:00 +0000\nMessage-ID: <m1@example.org>\nX-Gmail-Labels: Inbox\n\n"
    "Hi Bob, token " + FAKE + " inside.\n\n"
    "From b@example.com Mon Jan  1 00:00:00 2024\n"
    "From: Bob <bob@example.com>\nTo: alice@example.org\nSubject: Re: Hello\n"
    "Date: Tue, 02 Jan 2024 11:30:00 +0000\nMessage-ID: <m2@example.com>\nX-Gmail-Labels: Sent,Important\n\n"
    "Thanks Alice.\n")


def w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root):
    w(root / "My Activity/Search/MyActivity.json", json.dumps([
        {"header": "Search", "title": "Searched for tea " + FAKE, "titleUrl": "https://www.example.com/s?q=tea",
         "time": "2024-02-03T04:05:06.789Z", "note": "k " + FAKE},
        {"header": "Search", "title": "Searched for coffee", "time": "2024-02-04T00:00:00Z"}]))
    w(root / "My Activity/YouTube/MyActivity.json", json.dumps([
        {"header": "YouTube", "title": "Watched a video", "titleUrl": "https://youtu.be/abc",
         "time": "2024-03-01T12:00:00Z"}]))
    w(root / "My Activity/Maps/MyActivity.html", HTML)
    w(root / "Chrome/History.json", json.dumps({"Browser History": [
        {"title": "Example", "url": "https://www.example.org/a", "time_usec": 1709294400000000, "client_id": "dev1"},
        {"title": "No client", "url": "https://example.com/b", "time_usec": 1709298000000000}]}))
    w(root / "Location History/Records.json", json.dumps({"locations": [
        {"timestamp": "2024-04-01T10:00:00Z", "latitudeE7": 123456789, "longitudeE7": -987654321},
        {"timestampMs": "1712048400000", "latitudeE7": 1, "longitudeE7": 2}]}))
    w(root / "Timeline/Timeline.json", json.dumps({"semanticSegments": [
        {"startTime": "2024-04-02T08:00:00-07:00", "visit": {"topCandidate": {"placeId": "p1"}}}]}))
    w(root / "Calendar/cal.ics", ICS)
    w(root / "Mail/All mail.mbox", MBOX)
    w(root / "Broken/MyActivity.json", "{not json")
    w(root / ".secrets/My Activity/Search/MyActivity.json",
      json.dumps([{"title": "hidden", "time": "2024-01-01T00:00:00Z"}]))
    w(root / "Other/notes.txt", "ignored")


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / "export"
    build(root)
    return Config(db_path=tmp_path / "lake.duckdb", takeout_dir=root, claude_export_dir=tmp_path / "x",
                  claude_projects=tmp_path / "y", spotify_dir=tmp_path / "z", default_tz="UTC", travel=[])


def refresh(cfg):
    return cli.cmd_refresh(cfg, ["takeout"], write_schema=False)


def q(cfg, sql, *a):
    con = db.connect(cfg.db_path, read_only=True)
    try:
        return con.execute(sql, list(a)).fetchall()
    finally:
        con.close()


def counts(cfg):
    return dict(q(cfg, "SELECT source, count(*) FROM events GROUP BY 1"))


def test_row_counts(cfg):
    refresh(cfg)
    assert counts(cfg) == {
        "google.activity.search": 2, "google.activity.youtube": 1, "google.activity.maps": 2,
        "chrome_sync.visit": 2, "google.location": 3, "calendar.event": 2, "email": 2}


def test_timestamps(cfg):
    refresh(cfg)
    def ts(src, text):
        return q(cfg, "SELECT ts_utc FROM events WHERE source=? AND text LIKE ?", src, text)[0][0]

    assert ts("google.activity.search", "Searched for coffee") == datetime(2024, 2, 4, tzinfo=timezone.utc)
    assert ts("google.activity.maps", "Searched for%") == datetime(2024, 3, 5, 23, 4, 5, tzinfo=timezone.utc)
    assert ts("google.activity.maps", "Viewed area%") == datetime(2024, 3, 6, 9, tzinfo=timezone.utc)
    assert ts("calendar.event", "Planning call") == datetime(2024, 7, 10, 13, tzinfo=timezone.utc)  # EDT
    assert ts("calendar.event", "Offsite") == datetime(2024, 8, 1, 12, tzinfo=timezone.utc)
    assert ts("email", "Thanks%") == datetime(2024, 1, 2, 11, 30, tzinfo=timezone.utc)
    locs = [r[0] for r in q(cfg, "SELECT ts_utc FROM events WHERE source='google.location' ORDER BY 1")]
    assert locs == [datetime(2024, 4, 1, 10, tzinfo=timezone.utc),
                    datetime(2024, 4, 2, 9, tzinfo=timezone.utc), datetime(2024, 4, 2, 15, tzinfo=timezone.utc)]
    assert q(cfg, "SELECT min(ts_utc) FROM events WHERE source='chrome_sync.visit'")[0][0] == \
        datetime(2024, 3, 1, 12, tzinfo=timezone.utc)


def test_fields_and_payload_contract(cfg):
    refresh(cfg)
    r = q(cfg, "SELECT json_extract_string(payload,'$.product'), actor, device, domain FROM events "
               "WHERE source='google.activity.youtube'")
    assert r == [("YouTube", "me", None, "youtu.be")]
    assert q(cfg, "SELECT DISTINCT json_extract_string(payload,'$.product') FROM events "
                  "WHERE source='google.activity.maps'") == [("Maps",)]
    assert dict(q(cfg, "SELECT coalesce(device,'-'), count(*) FROM events "
                       "WHERE source='chrome_sync.visit' GROUP BY 1")) == {"dev1": 1, "-": 1}
    assert q(cfg, "SELECT DISTINCT device, actor FROM events WHERE source='google.location'") == [("phone", "me")]
    assert q(cfg, "SELECT domain FROM events WHERE source='chrome_sync.visit' AND device='dev1'") == [("example.org",)]
    rows = q(cfg, "SELECT actor, json_extract_string(payload,'$.from'), json_extract_string(payload,'$.to'), "
                  "json_extract_string(payload,'$.subject'), json_extract_string(payload,'$.labels'), "
                  "json_extract_string(payload,'$.sender_domain') FROM events WHERE source='email' ORDER BY ts_utc")
    assert rows[0] == ("other", "Alice <alice@example.org>", "bob@example.com", "Hello", "Inbox", "example.org")
    assert rows[1][0] == "me" and rows[1][4] == "Sent,Important" and rows[1][5] == "example.com"
    # raw location record stays in the payload
    assert q(cfg, "SELECT count(*) FROM events WHERE source='google.location' "
                  "AND json_extract(payload,'$.latitudeE7') IS NOT NULL")[0][0] == 2
    # html activity: link, general-area location
    pl = json.loads(q(cfg, "SELECT payload FROM events WHERE source='google.activity.maps' "
                           "AND text LIKE 'Searched%'")[0][0])
    assert pl["time_zone"] == "PST" and pl["lat"] == 12.345678 and pl["loc_label"].startswith("Springfield")
    assert q(cfg, "SELECT url FROM events WHERE text LIKE 'Searched for%cafe'") == \
        [("https://example.com/place?q=1&x=2",)]


def test_redaction_text_and_payload(cfg):
    out = refresh(cfg)
    assert out["sources"][0]["redactions"].get("github_token", 0) >= 3
    for col in ("text", "payload", "context"):
        assert q(cfg, f"SELECT count(*) FROM events WHERE {col} LIKE ?", f"%{FAKE}%")[0][0] == 0
    t = q(cfg, "SELECT text FROM events WHERE text LIKE 'Searched for tea%'")[0][0]
    assert "[REDACTED:github_token]" in t
    for (p,) in q(cfg, "SELECT payload FROM events WHERE source LIKE 'google.activity.search'"):
        json.loads(p)
    assert q(cfg, "SELECT count(*) FROM events WHERE source='google.activity.search' "
                  "AND json_extract_string(payload,'$.note') LIKE '%REDACTED%'")[0][0] == 1


def test_second_refresh_adds_nothing(cfg):
    first = refresh(cfg)
    assert first["rows_added"] == 14
    second = refresh(cfg)
    assert second["rows_added"] == 0
    assert second["sources"][0].get("files_loaded", 0) == 0


def test_overlapping_reexport_adds_nothing(cfg, tmp_path):
    refresh(cfg)
    shutil.copytree(cfg.takeout_dir, cfg.takeout_dir.parent / "export" / "Takeout-2")
    second = refresh(cfg)
    assert second["rows_added"] == 0
    assert second["sources"][0]["files_loaded"] > 0  # the new copies were read, rows deduped
    assert sum(counts(cfg).values()) == 14


def test_malformed_file_reported_rest_loads(cfg):
    out = refresh(cfg)
    res = out["sources"][0]
    assert res["files_failed"] == 1
    assert sum(counts(cfg).values()) == 14
    note = q(cfg, "SELECT note FROM runs WHERE source='takeout'")[0][0]
    assert "Broken/MyActivity.json" in note
    assert q(cfg, "SELECT count(*) FROM manifest WHERE file LIKE 'Broken/%'")[0][0] == 0


def test_secret_path_not_read(cfg, monkeypatch):
    seen = []
    real = db.sha256_file
    monkeypatch.setattr(tk.db, "sha256_file", lambda p: (seen.append(str(p)), real(p))[1])
    out = refresh(cfg)
    assert seen and not any(".secrets" in s for s in seen)
    assert out["sources"][0]["files_seen"] == 9  # secret file and notes.txt not counted
    assert q(cfg, "SELECT count(*) FROM events WHERE text='hidden' OR src_file LIKE '%secrets%'")[0][0] == 0
    assert q(cfg, "SELECT count(*) FROM manifest WHERE file LIKE '%secrets%'")[0][0] == 0


def test_missing_dir_and_dispatch(cfg, tmp_path):
    cfg.takeout_dir = tmp_path / "nope"
    assert tk.TakeoutSource(cfg, None).discover() == []
    assert tk.classify("Takeout/My Activity/Search/MyActivity.html") == "myactivity_html"
    assert tk.classify("Takeout/My Activity/Other/foo.json") == "activity"
    assert tk.classify("Takeout/Chrome/Bookmarks.json") is None
    assert tk.classify("Takeout/YouTube/history/watch-history.json") == "activity"
