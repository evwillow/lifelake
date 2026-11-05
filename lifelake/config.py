"""Paths and knobs. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# File names a connector must never read, at any depth.
NEVER_INGEST = frozenset({"pulse.csv"})

# Credential stores and secret folders: skipped by every directory-walking connector.
SECRET_PATH = re.compile(
    r"(^|[\\/])(\.secrets|\.ssh|\.gnupg|\.aws|\.azure|\.kube|\.docker|credentials?|passwords?|keys?|secrets?|"
    r"Login Data[^\\/]*|Cookies|Web Data|Local State|[^\\/]*cookies?[^\\/]*\.json|[^\\/]*password[^\\/]*\.json|"
    r"login_protection_data\.json|two-factor_authentication\.json|\.env[^\\/]*|[^\\/]*\.(pem|key|p12|pfx|kdbx))"
    r"([\\/]|$)", re.I)


def is_secret_path(path: str) -> bool:
    return bool(SECRET_PATH.search(path))


def _path(env: str, default: str | Path) -> Path:
    return Path(os.environ.get(env) or default).expanduser()


def parse_travel(spec: str | None) -> list[tuple[str, str, str]]:
    """``Zone:YYYY-MM-DD..YYYY-MM-DD`` items, comma separated -> [(zone, first, last_inclusive)]."""
    out = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        zone, _, span = item.partition(":")
        first, _, last = span.partition("..")
        if not (zone and first and last):
            raise ValueError(f"bad LIFELAKE_TRAVEL item {item!r}; want Zone:YYYY-MM-DD..YYYY-MM-DD")
        out.append((zone, first, last))
    return sorted(out, key=lambda t: t[1])


@dataclass
class Config:
    db_path: Path = field(default_factory=lambda: _path("LIFELAKE_DB", "lifelake.duckdb"))
    claude_export_dir: Path = field(default_factory=lambda: _path("LIFELAKE_CLAUDE_EXPORT_DIR", "data/claude_export"))
    claude_projects: Path = field(default_factory=lambda: _path("LIFELAKE_CLAUDE_PROJECTS", "~/.claude/projects"))
    takeout_dir: Path = field(default_factory=lambda: _path("LIFELAKE_TAKEOUT_DIR", "data/takeout"))
    spotify_dir: Path = field(default_factory=lambda: _path("LIFELAKE_SPOTIFY_DIR", "data/spotify"))
    # Zone for local_ts outside any travel period, and optional travel periods.
    default_tz: str = field(default_factory=lambda: os.environ.get("LIFELAKE_DEFAULT_TZ", "UTC"))
    travel: list[tuple[str, str, str]] = field(default_factory=lambda: parse_travel(os.environ.get("LIFELAKE_TRAVEL")))
    # Live jsonl files touched this recently are still being written; leave them for later.
    recent_seconds: int = field(default_factory=lambda: int(os.environ.get("LIFELAKE_RECENT_SECONDS", 1800)))
    # A running Claude Code session's own transcript is never ingested while it runs.
    exclude_session: str | None = field(default_factory=lambda: os.environ.get("CLAUDE_CODE_SESSION_ID") or None)
    workers: int = field(default_factory=lambda: int(
        os.environ.get("LIFELAKE_WORKERS", max(1, min(16, (os.cpu_count() or 2) - 2)))))
    # Per-field caps for tool payloads (characters, after redaction).
    tool_cap: int = 8000
    attach_cap: int = 20000

    @property
    def schema_path(self) -> Path:
        return self.db_path.with_name("SCHEMA.md")
