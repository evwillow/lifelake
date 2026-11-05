"""Timestamp and URL helpers shared by the connectors. Everything returns aware UTC datetimes or None."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_unix(x, unit: str = "s") -> datetime | None:
    if x in (None, "", 0, "0"):
        return None
    x = float(x)
    if unit == "ms":
        x /= 1000.0
    elif unit == "us":
        x /= 1e6
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def from_webkit(us) -> datetime | None:
    """Chrome timestamps: microseconds since 1601-01-01 UTC."""
    if not us:
        return None
    try:
        return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(us))
    except (OverflowError, ValueError):
        return None


_FORMATS = (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M", "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z", "%b %d, %Y, %I:%M:%S %p %Z", "%b %d, %Y, %I:%M:%S %p", "%Y%m%dT%H%M%SZ",
            "%Y%m%dT%H%M%S", "%Y%m%d")


def parse_any(s) -> datetime | None:
    """Parse ISO-ish strings, epoch numbers and mail dates; returns an aware datetime or None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        v = float(s)
        return from_unix(v, "ms" if v > 1e11 else "s")
    s = str(s).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,17}", s):
        v = int(s)
        if v > 1e16:
            return from_webkit(v)
        return from_unix(v, "us" if v > 1e14 else "ms" if v > 1e11 else "s")
    t = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    for f in _FORMATS:
        try:
            d = datetime.fromisoformat(t) if f is None else datetime.strptime(s if "%Z" in f or "%z" in f else t, f)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    if url.lower().startswith("mailto:"):
        return url[7:].split("@")[-1].lower().strip(">") or None
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/:?#]+)", url, re.I)
    if not m:
        return None
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h
