"""lifelake: refresh the lake, scan it for secrets, query it read-only, and write SCHEMA.md."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

from . import db
from .config import Config

SOURCES = ("claude_ai", "claude_code", "takeout", "spotify")


def source_class(name: str):
    if name == "claude_ai":
        from .sources.claude_ai import ClaudeAiSource
        return ClaudeAiSource
    if name == "claude_code":
        from .sources.claude_code import ClaudeCodeSource
        return ClaudeCodeSource
    if name == "takeout":
        from .sources.takeout import TakeoutSource
        return TakeoutSource
    if name == "spotify":
        from .sources.spotify import SpotifySource
        return SpotifySource
    raise SystemExit(f"unknown source {name!r}; choose from {', '.join(SOURCES)}")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def open_rw(cfg: Config):
    return db.connect(cfg.db_path, default_tz=cfg.default_tz, travel=cfg.travel)


def cmd_refresh(cfg: Config, names: list[str], write_schema: bool = True) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
    con = open_rw(cfg)
    results = []
    try:
        for name in names:
            log(f"refresh {name} ...")
            res = source_class(name)(cfg, con, log=log).refresh(run_id)
            results.append(res)
            log(f"  {name}: {res['seconds']}s, files seen {res.get('files_seen', 0)}, loaded "
                f"{res.get('files_loaded', 0)}, skipped {res.get('files_skipped', 0)}, rows added "
                f"{res.get('rows_added', 0)}, redactions {sum(res['redactions'].values())}")
        con.execute("CHECKPOINT")
        if write_schema:
            from .schema_doc import write_schema_md
            write_schema_md(con, cfg)
    finally:
        con.close()
    out = {"run_id": run_id, "rows_added": sum(r.get("rows_added", 0) for r in results), "sources": results}
    print(json.dumps(out, default=str))
    return out


def cmd_scan(cfg: Config) -> int:
    from .scan import scan_lake
    con = db.connect(cfg.db_path, read_only=True)
    try:
        res = scan_lake(con, workers=cfg.workers)
    finally:
        con.close()
    print(json.dumps(res))
    return 0 if res["hits"] == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lifelake", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="ingest new data (all sources, or one)")
    r.add_argument("source", nargs="?", choices=SOURCES)
    r.add_argument("--no-schema", action="store_true", help="skip rewriting SCHEMA.md")
    sub.add_parser("scan", help="scan all lake text with the regex set; exit 1 on any hit")
    sub.add_parser("schema", help="rewrite SCHEMA.md next to the database")
    sub.add_parser("context", help="print the safe schema with sample values (what an LLM gets to see)")
    q = sub.add_parser("query", help="run one read-only SELECT over the safe views")
    q.add_argument("sql")
    q.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    cfg = Config()
    if a.cmd == "refresh":
        cmd_refresh(cfg, [a.source] if a.source else list(SOURCES), write_schema=not a.no_schema)
        return 0
    if a.cmd == "scan":
        return cmd_scan(cfg)
    if a.cmd == "schema":
        from .schema_doc import write_schema_md
        con = open_rw(cfg)
        try:
            write_schema_md(con, cfg)
        finally:
            con.close()
        return 0
    from .query import Rejected, SafeLake, print_result
    lake = SafeLake(cfg.db_path)
    if a.cmd == "context":
        print(lake.context())
        return 0
    try:
        cols, rows = lake.run_sql(a.sql)
    except Rejected as e:
        print(f"REJECTED: {e}")
        return 2
    print_result(a.sql.strip(), cols, rows, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
