"""Source contract: discover -> watermark -> extract -> load, with a manifest row per input."""
from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .. import db
from ..config import NEVER_INGEST, Config


@dataclass
class Item:
    key: str                      # manifest.file
    path: Path | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Extracted:
    item: Item
    sha256: str
    watermark: str
    rows: dict[str, list[dict]]   # table -> rows
    counts: Counter               # redaction hits by type
    n: int = 0                    # rows extracted from this item
    note: str = ""


class Source:
    name = "base"
    batch_rows = 50_000

    def __init__(self, cfg: Config, con, log=print):
        self.cfg, self.con, self.log = cfg, con, log

    # --- contract -------------------------------------------------------------------
    def discover(self) -> list[Item]:
        raise NotImplementedError

    def watermark(self, item: Item) -> str:
        st = item.path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"

    def extract(self, items: list[tuple[Item, str]]) -> Iterator[Extracted]:
        """Yield one Extracted per (item, watermark). Must redact all text it returns."""
        raise NotImplementedError

    def load(self, rows: dict[str, list[dict]]) -> int:
        raise NotImplementedError

    # --- driver ---------------------------------------------------------------------
    def refresh(self, run_id: str) -> dict:
        started, t0 = datetime.now(timezone.utc), time.time()
        stats = Counter()
        counts: Counter = Counter()
        prev = db.manifest_get(self.con, self.name)
        todo = []
        for item in self.discover():
            if os.path.basename(str(item.key)) in NEVER_INGEST:
                stats["files_excluded"] += 1
                continue
            stats["files_seen"] += 1
            wm = self.watermark(item)
            if wm is None:  # e.g. still being written
                stats["files_deferred"] += 1
                continue
            if item.key in prev and prev[item.key][1] == wm:
                stats["files_skipped"] += 1
                continue
            todo.append((item, wm))
        pending: list[Extracted] = []
        pending_rows = 0
        for ex in self.extract(todo):
            old = prev.get(ex.item.key)
            if old and old[0] == ex.sha256:  # touched, content unchanged
                stats["files_skipped"] += 1
                db.manifest_put(self.con, self.name, ex.item.key, ex.sha256, ex.n, ex.watermark)
                continue
            pending.append(ex)
            pending_rows += ex.n
            if pending_rows >= self.batch_rows:
                self._flush(pending, stats, counts)
                pending, pending_rows = [], 0
        self._flush(pending, stats, counts)
        secs = time.time() - t0
        note = f"deferred={stats['files_deferred']}" if stats["files_deferred"] else ""
        failed = getattr(self, "failed", None)
        if failed:
            stats["files_failed"] = len(failed)
            note = (note + " " if note else "") + "failed=" + ",".join(failed)
        db.log_run(self.con, run_id, self.name, started, secs, stats, counts, note=note)
        return {"source": self.name, "seconds": round(secs, 1), **stats, "redactions": dict(counts)}

    def _flush(self, pending: list[Extracted], stats: Counter, counts: Counter) -> None:
        if not pending:
            return
        merged: dict[str, list[dict]] = {}
        for ex in pending:
            for t, rs in ex.rows.items():
                merged.setdefault(t, []).extend(rs)
        self.con.execute("BEGIN")
        try:
            stats["rows_added"] += self.load(merged)
            for ex in pending:
                db.manifest_put(self.con, self.name, ex.item.key, ex.sha256, ex.n, ex.watermark)
                stats["files_loaded"] += 1
                stats["rows_extracted"] += ex.n
                counts.update(ex.counts)
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.log(f"  {self.name}: +{stats['rows_added']} rows, {stats['files_loaded']} files loaded")


def iso_utc(s) -> datetime | None:
    """Parse ISO-8601 (with Z or offset) to an aware UTC datetime."""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s.astimezone(timezone.utc) if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = str(s).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def chunks(xs: Iterable, n: int):
    buf = []
    for x in xs:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
