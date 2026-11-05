import json

import pytest

from lifelake import db
from lifelake.cli import cmd_refresh
from lifelake.config import Config


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "sp" / "Spotify Account Data"
    d.mkdir(parents=True)
    return Config(db_path=tmp_path / "lake.duckdb", spotify_dir=tmp_path / "sp",
                  claude_export_dir=tmp_path / "x1", claude_projects=tmp_path / "x2", takeout_dir=tmp_path / "x3")


def w(cfg, name, obj, sub="Spotify Account Data"):
    p = cfg.spotify_dir / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return p


def q(cfg, sql, *a):
    con = db.connect(cfg.db_path, read_only=True)
    try:
        return con.execute(sql, list(a)).fetchall()
    finally:
        con.close()


FAKE = "ghp_" + "Zq9" * 12

ACCOUNT = [
    {"endTime": "2024-03-01 10:00", "artistName": "Band One", "trackName": "Song A", "msPlayed": 215340},
    {"endTime": "2024-03-01 10:04", "podcastName": "Show Two", "episodeName": "Ep 5", "msPlayed": 1200},
]
EXTENDED = [
    {"ts": "2024-04-02T08:00:00Z", "platform": "android", "ms_played": 187654, "skipped": False,
     "master_metadata_track_name": "Song B", "master_metadata_album_artist_name": "Band Three",
     "ip_addr": "203.0.113.9", "ip_addr_decrypted": "203.0.113.10", "user_agent_decrypted": "UA-marker-xyz",
     "username": "user-marker-xyz"},
    {"ts": "2024-04-02T08:05:00Z", "platform": "web", "ms_played": 5, "skipped": True,
     "episode_name": "Ep 9", "episode_show_name": "Show Four"},
    {"ts": "2024-04-02T08:06:00Z", "platform": "web", "ms_played": 0, "skipped": None,
     "master_metadata_track_name": None, "master_metadata_album_artist_name": None},
]


def populate(cfg):
    w(cfg, "StreamingHistory0.json", ACCOUNT)
    w(cfg, "Streaming_History_Audio_2024_0.json", EXTENDED, "Spotify Extended")
    w(cfg, "SearchQueries.json", [{"searchTime": "2024-05-03T12:00:00.000Z[UTC]", "platform": "iOS",
                                   "searchQuery": f"lyrics token={FAKE}",
                                   "searchInteractionURIs": ["spotify:track:abc"]}])
    w(cfg, "Playlist1.json", {"playlists": [{"name": "Mix", "lastModifiedDate": "2024-06-01",
                                             "items": [{"t": 1}, {"t": 2}, {"t": 3}]}]})
    w(cfg, "Inferences.json", {"inferences": ["Likes jazz", "Commutes"]})
    w(cfg, "YourLibrary.json", {"tracks": [{"artist": "Band One", "track": "Song A"}]})
    w(cfg, "Follow.json", {"followerCount": 3})


def test_rows_and_fields(cfg):
    populate(cfg)
    res = cmd_refresh(cfg, ["spotify"], write_schema=False)
    assert res["rows_added"] == 2 + 3 + 1 + 1 + 2 + 1 + 1
    counts = dict(q(cfg, "select source, count(*) from events group by 1"))
    assert counts == {"spotify.play": 5, "spotify.search": 1, "spotify.playlist": 1, "spotify.inference": 2,
                      "spotify.other.yourlibrary": 1, "spotify.other.follow": 1}
    acct = q(cfg, "select text, context, payload, actor, device, ts_utc from events where text='Song A'")[0]
    assert acct[1] == "Spotify Band One" and acct[3] == "me" and acct[4] == "cloud"
    assert json.loads(acct[2]) == {"artist": "Band One", "ms_played": 215340, "platform": None, "skipped": None}
    assert acct[5].year == 2024
    pod = q(cfg, "select context, text from events where text='Ep 5'")[0]
    assert pod == ("Spotify Show Two", "Ep 5")
    ext = json.loads(q(cfg, "select payload from events where text='Song B'")[0][0])
    assert ext == {"artist": "Band Three", "ms_played": 187654, "platform": "android", "skipped": False}
    # null-name rows kept
    assert q(cfg, "select count(*) from events where source='spotify.play' and text is null")[0][0] == 1
    s = q(cfg, "select ts_utc, device, text, payload from events where source='spotify.search'")[0]
    assert s[0].month == 5 and s[1] == "iOS" and json.loads(s[3]) == {"clicked": ["spotify:track:abc"]}
    p = q(cfg, "select source_ref, ts_utc, payload from events where source='spotify.playlist'")[0]
    assert p[0] == "Mix" and p[1].month == 6 and json.loads(p[2]) == {"items": 3}
    inf = q(cfg, "select actor, ts_utc from events where source='spotify.inference'")
    assert inf == [("system", None), ("system", None)]


def test_identity_fields_never_stored(cfg):
    populate(cfg)
    cmd_refresh(cfg, ["spotify"], write_schema=False)
    blob = " ".join(str(v) for row in q(cfg, "select * from events") for v in row)
    for marker in ("203.0.113", "UA-marker-xyz", "user-marker-xyz", "ip_addr", "user_agent", "username"):
        assert marker not in blob


def test_secret_in_search_redacted(cfg):
    populate(cfg)
    res = cmd_refresh(cfg, ["spotify"], write_schema=False)
    text = q(cfg, "select text from events where source='spotify.search'")[0][0]
    assert FAKE not in text and "[REDACTED:" in text
    assert sum(res["sources"][0]["redactions"].values()) >= 1


def test_second_refresh_and_overlap_add_nothing(cfg):
    populate(cfg)
    cmd_refresh(cfg, ["spotify"], write_schema=False)
    assert cmd_refresh(cfg, ["spotify"], write_schema=False)["rows_added"] == 0
    # a later export containing the same plays in a different file
    w(cfg, "StreamingHistory0.json", ACCOUNT, "Spotify Account Data 2")
    w(cfg, "Streaming_History_Audio_2024_0.json", EXTENDED, "Spotify Extended 2")
    assert cmd_refresh(cfg, ["spotify"], write_schema=False)["rows_added"] == 0
    assert q(cfg, "select count(*) from events")[0][0] == 11


def test_userdata_and_payments_not_read(cfg):
    populate(cfg)
    w(cfg, "Userdata.json", {"username": "identity-marker-abc", "email": "me@example.com"})
    w(cfg, "Payments.json", {"card": "billing-marker-abc"})
    w(cfg, "Other.json", [{"x": "other-marker-abc"}])
    res = cmd_refresh(cfg, ["spotify"], write_schema=False)
    assert res["sources"][0]["files_seen"] == 7
    assert q(cfg, "select count(*) from manifest where file ilike '%userdata%' or file ilike '%payments%'")[0][0] == 0
    blob = " ".join(str(v) for row in q(cfg, "select * from events") for v in row)
    for marker in ("identity-marker", "billing-marker", "other-marker", "example.com"):
        assert marker not in blob


def test_malformed_file_reported_and_others_load(cfg):
    populate(cfg)
    w(cfg, "Playlist2.json", "{not json")
    res = cmd_refresh(cfg, ["spotify"], write_schema=False)
    r = res["sources"][0]
    assert r["files_failed"] == 1 and r["rows_added"] == 11
    assert q(cfg, "select note from runs where note like '%failed=%Playlist2.json%'")


def test_missing_dir(cfg, tmp_path):
    cfg.spotify_dir = tmp_path / "nope"
    assert cmd_refresh(cfg, ["spotify"], write_schema=False)["rows_added"] == 0
