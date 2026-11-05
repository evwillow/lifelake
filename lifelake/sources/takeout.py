"""Google Takeout connector: My Activity (JSON and HTML), Chrome sync history, Location History and
Timeline, calendar (.ics) and mail (.mbox) exports, all into the shared ``events`` table."""
from __future__ import annotations

import html as _html
import json
import mailbox
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import db
from ..config import is_secret_path
from ..timeparse import from_unix, parse_any
from .base import Extracted, Item, Source
from .events import event_row, load_events

_TZ_ABBR = {"PDT": -7, "PST": -8, "MDT": -6, "MST": -7, "CDT": -5, "CST": -6, "EDT": -4, "EST": -5, "HST": -10,
            "AKDT": -8, "AKST": -9, "UTC": 0, "GMT": 0, "BST": 1, "CET": 1, "CEST": 2, "JST": 9, "KST": 9,
            "HKT": 8, "SGT": 8}
_CELL = re.compile(r'<div class="outer-cell[^"]*">(.*?)(?=<div class="outer-cell|\Z)', re.S)
_ATIME = re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s?[AP]M) ([A-Z]{2,4})")
_NNBSP = " "
_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _strip(h: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", h)).replace(_NNBSP, " ").strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "unknown"


def classify(rel: str) -> str | None:
    """Which parser handles a file, from its path relative to the export root (None = not recognized)."""
    low = "/" + rel.lower().replace("\\", "/")
    if low.endswith("myactivity.json") or ("/my activity/" in low and low.endswith(".json")):
        return "activity"
    if low.endswith("myactivity.html") or ("/my activity/" in low and low.endswith(".html")):
        return "myactivity_html"
    if "/chrome/" in low and low.endswith("history.json"):
        return "chrome"
    if low.endswith("watch-history.json") or low.endswith("search-history.json"):
        return "activity"
    if low.endswith(".ics"):
        return "ics"
    if low.endswith(".mbox"):
        return "mbox"
    if ("timeline" in low or "location history" in low) and low.endswith(".json"):
        return "location"
    return None


class TakeoutSource(Source):
    name = "takeout"

    def __init__(self, cfg, con, log=print):
        super().__init__(cfg, con, log)
        self.failed: list[str] = []

    def discover(self) -> list[Item]:
        root = Path(self.cfg.takeout_dir)
        if not root.is_dir():
            return []
        items = []
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if is_secret_path(rel) or not p.is_file():
                continue
            kind = classify(rel)
            if kind:
                items.append(Item(key=rel, path=p, meta={"kind": kind}))
        return items

    def extract(self, items):
        for item, wm in items:
            try:
                sha = db.sha256_file(item.path)
                counts: Counter = Counter()
                rows = getattr(self, "_x_" + item.meta["kind"])(item, counts)
            except Exception as e:  # keep going; the failure is reported in the run note
                self.failed.append(item.key)
                self.log(f"  takeout: failed {item.key}: {type(e).__name__}")
                continue
            yield Extracted(item, sha, wm, {"events": rows}, counts, n=len(rows))

    def load(self, rows):
        return load_events(self.con, rows.get("events", []))

    # --- parsers ----------------------------------------------------------------------
    @staticmethod
    def _json(item):
        with open(item.path, encoding="utf-8") as f:
            return json.load(f)

    def _x_activity(self, item, counts):
        data = self._json(item)
        product = item.path.parent.name
        src = f"google.activity.{_slug(product)}"
        out = []
        for i, o in enumerate(data):
            out.append(event_row(
                counts, source=src, src_file=item.key, source_ref=i, ts=parse_any(o.get("time")), actor="me",
                context=f"Google {o.get('header') or product}", text=o.get("title"), url=o.get("titleUrl"),
                payload={**o, "product": product}))
        return out

    def _x_chrome(self, item, counts):
        data = self._json(item)
        return [event_row(counts, source="chrome_sync.visit", src_file=item.key, source_ref=i,
                          ts=from_unix(o.get("time_usec"), "us"), device=o.get("client_id") or None, actor="me",
                          context="Chrome synced history (all devices)", text=o.get("title"), url=o.get("url"),
                          payload=o)
                for i, o in enumerate(data.get("Browser History", []))]

    def _x_location(self, item, counts):
        data = self._json(item)
        items = (data.get("semanticSegments") or data.get("timelineObjects") or data.get("locations")
                 or (data if isinstance(data, list) else [])) if isinstance(data, (dict, list)) else []
        out = []
        for i, o in enumerate(items):
            ts = (o.get("startTime") or o.get("timestamp")
                  or (o.get("placeVisit") or o.get("activitySegment") or {}).get("duration", {}).get("startTimestamp"))
            if not ts and o.get("timestampMs"):
                ts = int(o["timestampMs"])
            out.append(event_row(
                counts, source="google.location", src_file=item.key, source_ref=i, ts=parse_any(ts),
                device="phone", actor="me", context="Location",
                text=json.dumps(o.get("visit") or o.get("placeVisit") or o.get("activity") or {}, default=str)[:2000],
                payload=o))
        return out

    def _x_ics(self, item, counts):
        with open(item.path, encoding="utf-8", errors="replace") as f:
            lines = f.read().replace("\r\n ", "").replace("\n ", "").splitlines()
        out, cur = [], None
        for line in lines:
            line = line.rstrip("\r")
            if line == "BEGIN:VEVENT":
                cur = {}
            elif line == "END:VEVENT" and cur is not None:
                start = parse_any(cur.get("DTSTART"))
                if start and cur.get("DTSTART_TZID") and not cur["DTSTART"].endswith("Z"):
                    try:
                        start = start.replace(tzinfo=ZoneInfo(cur["DTSTART_TZID"]))
                    except Exception:
                        pass
                out.append(event_row(
                    counts, source="calendar.event", src_file=item.key, source_ref=cur.get("UID"), ts=start,
                    actor="me", context=f"Calendar {item.path.name}", text=cur.get("SUMMARY"), payload=cur))
                cur = None
            elif cur is not None and ":" in line:
                k, v = line.split(":", 1)
                params = k.split(";")
                k = params[0]
                cur[k] = v
                for prm in params[1:]:
                    if prm.startswith("TZID="):
                        cur[k + "_TZID"] = prm[5:]
        return out

    def _x_mbox(self, item, counts):
        out = []
        for i, m in enumerate(mailbox.mbox(str(item.path))):
            body = None
            try:
                if m.is_multipart():
                    for part in m.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", "replace")
                            break
                else:
                    body = m.get_payload(decode=True).decode(m.get_content_charset() or "utf-8", "replace")
            except Exception:
                pass
            frm, to = str(m.get("From", "")), str(m.get("To", ""))
            labels = str(m.get("X-Gmail-Labels", ""))
            dom = (re.findall(r"@([\w.-]+)", frm) or [None])[0]
            out.append(event_row(
                counts, source="email", src_file=item.key, source_ref=m.get("Message-ID") or i,
                ts=parse_any(m.get("Date")), actor="me" if "Sent" in labels else "other",
                context=f"Email {frm[:80]}: {str(m.get('Subject', ''))[:120]}", text=(body or "")[:100000],
                url="mailto:" + (dom or ""),
                payload={"from": frm, "to": to, "subject": str(m.get("Subject", "")), "labels": labels,
                         "sender_domain": dom}))
        return out

    def _x_myactivity_html(self, item, counts):
        txt = Path(item.path).read_text(encoding="utf-8", errors="replace")
        folder = item.path.parent.name
        out = []
        for i, cell in enumerate(_CELL.findall(txt)):
            prod = _strip((re.search(r'mdl-typography--title">(.*?)<', cell) or [None, ""])[1]) or folder
            body = (re.search(r'content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)</div>',
                              cell, re.S) or [None, ""])[1]
            tm = _ATIME.search(_strip(body).replace(_NNBSP, " "))
            et = None
            if tm:
                try:
                    naive = datetime.strptime(tm.group(1).replace(_NNBSP, " "), "%b %d, %Y, %I:%M:%S %p")
                    et = naive.replace(tzinfo=timezone(timedelta(hours=_TZ_ABBR.get(tm.group(2), 0))))
                except ValueError:
                    et = None
            action = _strip(re.sub(r"<br>.*$", "", body, flags=re.S)) if body else None
            link = re.search(r'href="([^"]+)"', body)
            loc = re.search(r"center=(-?\d+\.\d+),(-?\d+\.\d+)", cell)
            cap = _strip((re.search(r'mdl-typography--caption">(.*?)</div>', cell, re.S) or [None, ""])[1])
            pl = {"product": prod, "time_zone": tm.group(2) if tm else None}
            if loc:
                pl.update(lat=float(loc.group(1)), lon=float(loc.group(2)),
                          loc_label=(re.search(r"Locations:\s*(.*?)(?:Why is this here\?|$)", cap, re.S)
                                     or [None, ""])[1].strip()[:200])
            out.append(event_row(
                counts, source=f"google.activity.{_slug(prod)}", src_file=item.key, source_ref=i, ts=et,
                actor="me", context=f"Google {prod} activity", text=(action or "")[:2000] or None,
                url=_html.unescape(link.group(1)) if link else None, payload=pl))
        return out
