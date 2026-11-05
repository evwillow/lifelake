"""Spotify connector: account-data exports and the extended streaming history, into ``events``.

Only a fixed list of file names is read. ``Userdata.json`` and ``Payments.json`` hold account identity
and billing data, so they (and every other unrecognised file) are never opened. The extended history's
IP address, user agent and username fields are never copied into a row.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterator

from ..config import is_secret_path
from ..timeparse import parse_any
from .base import Extracted, Item, Source
from .events import event_row, load_events

_EXACT = {"searchqueries.json", "inferences.json", "yourlibrary.json", "follow.json", "marquee.json"}
DEVICE = "cloud"


def _kind(name: str) -> str | None:
    n = name.lower()
    if not n.endswith(".json"):
        return None
    if n.startswith("streaming_history_audio_"):
        return "extended"
    if n.startswith("streaminghistory"):
        return "account"
    if n.startswith("playlist"):
        return "playlist"
    if n in _EXACT:
        return n[:-5]
    return None


class SpotifySource(Source):
    name = "spotify"

    def __init__(self, cfg, con, log=print):
        super().__init__(cfg, con, log)
        self.failed: list[str] = []

    def discover(self) -> list[Item]:
        root = Path(self.cfg.spotify_dir)
        if not root.is_dir():
            return []
        items = []
        for dirpath, _dirs, files in os.walk(root):
            for fn in sorted(files):
                kind = _kind(fn)
                if kind is None:
                    continue
                p = Path(dirpath) / fn
                rel = p.relative_to(root).as_posix()
                if is_secret_path(rel):
                    continue
                items.append(Item(key=rel, path=p, meta={"kind": kind}))
        return sorted(items, key=lambda i: i.key)

    def load(self, rows: dict[str, list[dict]]) -> int:
        return load_events(self.con, rows.get("events", []))

    def extract(self, items: list[tuple[Item, str]]) -> Iterator[Extracted]:
        self.failed = []
        for item, wm in items:
            try:
                raw = item.path.read_bytes()
                data = json.loads(raw.decode("utf-8-sig"))
                counts: Counter = Counter()
                rows = self._rows(item, data, counts)
            except (OSError, ValueError, AttributeError, TypeError) as e:
                self.log(f"  spotify: cannot parse {item.key}: {type(e).__name__}")
                self.failed.append(item.key)
                continue
            yield Extracted(item, hashlib.sha256(raw).hexdigest(), wm, {"events": rows}, counts, n=len(rows))

    # --- parsing --------------------------------------------------------------------
    def _rows(self, item: Item, d, counts: Counter) -> list[dict]:
        kind, key = item.meta["kind"], item.key
        recs = d if isinstance(d, list) else d.get("playlists") or d.get("tracks") or d.get("inferences") or [d]
        out = []

        def add(**kw):
            out.append(event_row(counts, source=kw.pop("source"), src_file=key, **kw))

        for i, r in enumerate(recs):
            if kind == "extended":
                artist = r.get("master_metadata_album_artist_name") or r.get("episode_show_name")
                add(source="spotify.play", source_ref=i, ts=parse_any(r.get("ts")), device=DEVICE, actor="me",
                    context=f"Spotify {artist or ''}".strip(),
                    text=r.get("master_metadata_track_name") or r.get("episode_name"),
                    payload={"artist": artist, "ms_played": r.get("ms_played"),
                             "platform": r.get("platform"), "skipped": r.get("skipped")})
            elif kind == "account":
                artist = r.get("artistName") or r.get("podcastName")
                add(source="spotify.play", source_ref=i, ts=parse_any(r.get("endTime")), device=DEVICE, actor="me",
                    context=f"Spotify {artist or ''}".strip(), text=r.get("trackName") or r.get("episodeName"),
                    payload={"artist": artist, "ms_played": r.get("msPlayed"), "platform": None, "skipped": None})
            elif kind == "searchqueries":
                add(source="spotify.search", source_ref=i,
                    ts=parse_any(re.sub(r"\[[^\]]*\]$", "", r.get("searchTime") or "")),
                    device=r.get("platform") or DEVICE, actor="me", context="Spotify search",
                    text=r.get("searchQuery"), payload={"clicked": r.get("searchInteractionURIs")})
            elif kind == "playlist":
                add(source="spotify.playlist", source_ref=r.get("name") or i, ts=parse_any(r.get("lastModifiedDate")),
                    device=DEVICE, actor="me", context="Spotify playlist", text=r.get("name"),
                    payload={"items": len(r.get("items") or [])})
            elif kind == "inferences":
                add(source="spotify.inference", source_ref=i, device=DEVICE, actor="system",
                    context="Spotify inference", text=r if isinstance(r, str) else json.dumps(r)[:300])
            else:
                stem = os.path.splitext(os.path.basename(key))[0].lower()
                add(source=f"spotify.other.{stem}", source_ref=i, device=DEVICE, actor="me",
                    context=f"Spotify {os.path.basename(key)}", text=None,
                    payload=r if isinstance(r, dict) else {"value": r})
        return out
