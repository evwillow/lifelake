"""Row builder and loader for the shared ``events`` table (Takeout, Spotify, future connectors).

Every text value is redacted here, so a connector cannot forget. ``event_id`` hashes the source, the
time and the redacted content (not the file), so overlapping exports dedupe against each other.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime

import pyarrow as pa

from .. import db
from ..redact import redact
from ..timeparse import domain_of, iso

TS = pa.timestamp("us", tz="UTC")
EVENTS = pa.schema([
    ("event_id", pa.string()), ("source", pa.string()), ("source_ref", pa.string()), ("ts_utc", TS),
    ("local_ts", pa.timestamp("us")), ("tz", pa.string()), ("device", pa.string()), ("actor", pa.string()),
    ("context", pa.string()), ("text", pa.string()), ("url", pa.string()), ("domain", pa.string()),
    ("payload", pa.string()), ("src_file", pa.string())])


def redact_obj(o, counts: Counter):
    """Redact every string leaf of a JSON-like value, so the result is still valid JSON when dumped."""
    if isinstance(o, str):
        return redact(o, counts)
    if isinstance(o, dict):
        return {k: redact_obj(v, counts) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [redact_obj(v, counts) for v in o]
    return o


def event_row(counts: Counter, *, source: str, src_file: str, source_ref=None, ts: datetime | None = None,
              device: str | None = None, actor: str | None = None, context: str | None = None,
              text: str | None = None, url: str | None = None, payload: dict | None = None) -> dict:
    """Build one ``events`` row. Rows without a timestamp are kept with ts_utc NULL; nothing is dropped."""
    context, text, url = redact(context, counts), redact(text, counts), redact(url, counts, entropy=False)
    body = json.dumps(redact_obj(payload or {}, counts), default=str, ensure_ascii=False, sort_keys=True)
    key = f"{source}|{iso(ts)}|{text or ''}|{url or ''}|{body}"
    return {"event_id": hashlib.sha256(key.encode()).hexdigest()[:32], "source": source,
            "source_ref": None if source_ref is None else str(source_ref), "ts_utc": ts, "local_ts": None, "tz": None,
            "device": device, "actor": actor, "context": context, "text": text, "url": url,
            "domain": domain_of(url), "payload": body, "src_file": src_file}


def load_events(con, rows: list[dict]) -> int:
    return db.insert_staged(con, "events", rows, EVENTS, ["event_id"],
                            select="s.* EXCLUDE (local_ts, tz), timezone(p.tz, s.ts_utc) AS local_ts, p.tz AS tz")
