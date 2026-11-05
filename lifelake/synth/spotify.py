"""Spotify export: account and extended streaming history, searches, playlists, inferences, library, account data."""
from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta

from . import text as tx
from .core import (
    HARD_END,
    SNAPSHOT,
    UTC,
    Planter,
    Tree,
    Zipf,
    categorical,
    in_trip,
    iso_ms,
    iso_s,
    lognormal,
    moments,
    poisson,
    rand_str,
    scaled,
    span,
)
from .params import PARAMS

ROOT = "spotify"
# Values that exist only in fields the connector must never store. Tests look for these in the lake.
USERDATA_MARKERS = ("sam-userdata-handle", "userdata-marker@example.org", "+1-555-0100-marker")
EXTENDED_MARKERS = ("sam-extended-handle", "198.51.100.", "DemoPlayer/1.0-marker")


def _catalogue(rng: random.Random) -> list[tuple[str, Zipf]]:
    pairs = [f"{a} {n}" for a in tx.ARTIST_ADJECTIVES for n in tx.ARTIST_NOUNS]
    names = rng.sample(pairs, min(PARAMS["n_artists"], len(pairs)))
    out = []
    for name in names:
        n = 1 + poisson(rng, PARAMS["tracks_per_artist"])
        words = rng.sample(tx.TRACK_WORDS, min(n, len(tx.TRACK_WORDS)))
        tracks = [f"{w} {i + 1}" if rng.random() < 0.2 else w for i, w in enumerate(words)]
        out.append((name, Zipf(tracks, PARAMS["track_zipf"])))
    return out


def _plays(rng, scale: float, artists: Zipf) -> list[dict]:
    plays: list[dict] = []
    for start in moments(rng, scaled("listening_sessions", scale)):
        t = start.replace(microsecond=0)
        platform = categorical(rng, PARAMS["spotify_platforms"])
        for _ in range(1 + poisson(rng, PARAMS["plays_per_session"])):
            length = int(lognormal(rng, PARAMS["track_ms"]))
            draw = rng.random()
            artist, track, podcast = None, None, False
            skipped = False
            if draw < PARAMS["nameless_play"]:
                ms = 0
            else:
                if draw < PARAMS["nameless_play"] + PARAMS["podcast"]:
                    artist, track, podcast = rng.choice(tx.PODCASTS), f"Episode {rng.randrange(1, 200)}", True
                else:
                    artist, tracks = artists.draw(rng)
                    track = tracks.draw(rng)
                skipped = rng.random() < PARAMS["skip"]
                ms = int(length * rng.uniform(*PARAMS["skip_fraction"])) + 1 if skipped else length
            plays.append({"end": t, "artist": artist, "track": track, "podcast": podcast, "ms": ms, "skipped": skipped,
                          "platform": platform})
            t = t + timedelta(milliseconds=ms) + timedelta(seconds=rng.randrange(1, 30))
            t = t.replace(microsecond=0)
            if t >= HARD_END:
                break
    return plays


def _account_row(p: dict) -> dict:
    t = p["end"]
    end = f"{t.year:04}-{t.month:02}-{t.day:02} {t.hour:02}:{t.minute:02}"
    if p["podcast"]:
        return {"endTime": end, "podcastName": p["artist"], "episodeName": p["track"], "msPlayed": p["ms"]}
    return {"endTime": end, "artistName": p["artist"], "trackName": p["track"], "msPlayed": p["ms"]}


def _extended_row(rng, p: dict) -> dict:
    return {
        "ts": iso_s(p["end"]), "username": EXTENDED_MARKERS[0], "platform": p["platform"], "ms_played": p["ms"],
        "conn_country": "ZZ", "ip_addr": EXTENDED_MARKERS[1] + str(rng.randrange(1, 255)),
        "ip_addr_decrypted": EXTENDED_MARKERS[1] + str(rng.randrange(1, 255)),
        "user_agent_decrypted": EXTENDED_MARKERS[2],
        "master_metadata_track_name": None if p["podcast"] else p["track"],
        "master_metadata_album_artist_name": None if p["podcast"] else p["artist"],
        "master_metadata_album_album_name": None if p["podcast"] or not p["track"] else p["track"] + " (album)",
        "spotify_track_uri": None if p["podcast"] or not p["track"] else "spotify:track:" + rand_str(rng, 22),
        "episode_name": p["track"] if p["podcast"] else None,
        "episode_show_name": p["artist"] if p["podcast"] else None,
        "spotify_episode_uri": "spotify:episode:" + rand_str(rng, 22) if p["podcast"] else None,
        "reason_start": "trackdone", "reason_end": "fwdbtn" if p["skipped"] else "trackdone", "shuffle": False,
        "skipped": p["skipped"], "offline": False, "offline_timestamp": 0, "incognito_mode": False,
    }


def write_spotify(rng: random.Random, tree: Tree, plant: Planter, scale: float) -> dict:
    catalogue = _catalogue(rng)
    artists = Zipf(catalogue, PARAMS["artist_zipf"])
    cut = date.fromisoformat(PARAMS["extended_until"])
    plays = _plays(rng, scale, artists)

    # The two history files never hold the same play: one covers the time before the cut, one the rest.
    # The account file has minute resolution, so plays that collapse to one row there are dropped here.
    extended, account, kept, seen = [], [], [], set()
    for p in plays:
        if p["end"].date() < cut:
            row, key = _extended_row(rng, p), (p["end"], p["track"], p["artist"], p["ms"], p["platform"], p["skipped"])
            bucket = extended
        else:
            row = _account_row(p)
            key = (row["endTime"], p["track"], p["artist"], p["ms"])
            p = {**p, "end": p["end"].replace(second=0), "skipped": None}
            bucket = account
        if key in seen:
            continue
        seen.add(key)
        bucket.append(row)
        kept.append(p)
    last_ext = max((p["end"] for p in kept if p["end"].date() < cut), default=SNAPSHOT)
    last_acc = max((p["end"] for p in kept if p["end"].date() >= cut), default=SNAPSHOT)
    tree.write_json(f"{ROOT}/Streaming_History_Audio_2024_0.json", extended, last_ext)
    tree.write_json(f"{ROOT}/StreamingHistory0.json", account, last_acc)

    named: Counter = Counter(p["artist"] for p in kept if p["artist"])
    top_artist, top_plays = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    searches, search_times, seen_q = [], [], set()
    for t in moments(rng, scaled("n_spotify_searches", scale)):
        q = plant.field("spotify.search", rng.choice(tx.SPOTIFY_QUERIES), single_line=True)
        if (t, q) in seen_q:
            continue
        seen_q.add((t, q))
        searches.append({"platform": categorical(rng, PARAMS["spotify_platforms"]), "searchTime": iso_ms(t) + "[UTC]",
                         "searchQuery": q, "searchInteractionURIs": ["spotify:track:" + rand_str(rng, 22)]})
        search_times.append(t)
    tree.write_json(f"{ROOT}/SearchQueries.json", searches, max(search_times))

    n_playlists = scaled("n_playlists", max(scale, 0.25))
    playlists, last_mod = [], []
    for name in rng.sample(tx.TRACK_WORDS, min(n_playlists, len(tx.TRACK_WORDS))):
        d = date(2024, rng.randrange(1, 13), rng.randrange(1, 29))
        items = [{"track": {"trackName": rng.choice(tx.TRACK_WORDS), "artistName": rng.choice(catalogue)[0]},
                  "episode": None, "addedDate": d.isoformat()}
                 for _ in range(1 + poisson(rng, PARAMS["playlist_items"]))]
        playlists.append({"name": plant.field("spotify.playlist", name + " mix", single_line=True),
                          "lastModifiedDate": d.isoformat(), "items": items})
        last_mod.append(datetime(d.year, d.month, d.day, tzinfo=UTC))
    tree.write_json(f"{ROOT}/Playlist1.json", {"playlists": playlists}, max(last_mod))

    n_inf = min(scaled("n_inferences", max(scale, 0.2)), len(tx.INFERENCES))
    inferences = [plant.field("spotify.inference", s, single_line=True) for s in rng.sample(tx.INFERENCES, n_inf)]
    tree.write_json(f"{ROOT}/Inferences.json", {"inferences": inferences}, SNAPSHOT)

    wanted = scaled("n_library", scale)
    library, taken = [], set()
    for _ in range(wanted * 20):  # popular tracks repeat under the Zipf draws, so try more often than needed
        if len(library) >= wanted:
            break
        artist, tracks = artists.draw(rng)
        track = tracks.draw(rng)
        if (artist, track) in taken:
            continue
        taken.add((artist, track))
        library.append({"artist": artist, "album": track + " (album)",
                        "track": plant.field("spotify.library", track, single_line=True),
                        "uri": "spotify:track:" + rand_str(rng, 22)})
    tree.write_json(f"{ROOT}/YourLibrary.json", {"tracks": library, "albums": [], "shows": [], "episodes": [],
                                                 "artists": [], "bannedTracks": []}, SNAPSHOT)

    # Account identity data: the connector must never open this file.
    tree.write_json(f"{ROOT}/Userdata.json", {
        "username": USERDATA_MARKERS[0], "email": USERDATA_MARKERS[1], "country": "ZZ", "birthdate": "1990-01-01",
        "gender": "neutral", "postalCode": "00000", "mobileNumber": USERDATA_MARKERS[2],
        "creationTime": "2020-01-01"}, SNAPSHOT)

    return {
        "spotify_plays": len(kept), "spotify_ms_played": sum(p["ms"] for p in kept),
        "spotify_plays_extended": len(extended), "spotify_plays_account": len(account),
        "spotify_skipped": sum(p["skipped"] is True for p in kept),
        "spotify_nameless_plays": sum(p["track"] is None for p in kept),
        "spotify_podcast_plays": sum(p["podcast"] for p in kept),
        "spotify_top_artist": top_artist, "spotify_top_artist_plays": top_plays,
        "spotify_plays_in_trip": sum(in_trip(p["end"].date()) for p in kept),
        "spotify_ts_range": span(p["end"] for p in kept),
        "spotify_searches": len(searches), "spotify_playlists": len(playlists), "spotify_inferences": len(inferences),
        "spotify_library": len(library), "spotify_files": 6,
        "userdata_markers": list(USERDATA_MARKERS), "extended_markers": list(EXTENDED_MARKERS),
    }
