"""claude.ai conversations from claude_all_transcripts*.parquet (rows with source = 'claude.ai').

That parquet also carries a claude_code copy; the claude_code source owns those rows.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .. import db
from ..redact import redact
from .base import Extracted, Item, Source, iso_utc

TS = pa.timestamp("us", tz="UTC")
CONV = pa.schema([
    ("conversation_id", pa.string()), ("name", pa.string()), ("created_utc", TS), ("updated_utc", TS),
    ("local_created_ts", pa.timestamp("us")), ("tz", pa.string()), ("platform", pa.string()),
    ("is_starred", pa.bool_()), ("summary", pa.string()), ("src_file", pa.string())])
MSG = pa.schema([
    ("message_id", pa.string()), ("conversation_id", pa.string()), ("parent_id", pa.string()),
    ("role", pa.string()), ("message_index", pa.int32()), ("ts_utc", TS), ("local_ts", pa.timestamp("us")),
    ("tz", pa.string()), ("model", pa.string()), ("truncated", pa.bool_()), ("text", pa.string()),
    ("attachments", pa.string()), ("files", pa.string()), ("src_file", pa.string())])


def _cap(s: str | None, n: int) -> str | None:
    if s is None or len(s) <= n:
        return s
    return s[:n] + f"…[+{len(s) - n} chars]"


def parse_rows(d: dict, src: str, counts: Counter, attach_cap: int) -> tuple[dict, dict]:
    """One parquet row -> (message row, conversation row)."""
    x = json.loads(d["extra_json"]) if d.get("extra_json") else {}
    atts = x.get("attachments") or []
    att_txt = "\n\n".join(
        f"[{a.get('file_name') or a.get('file_type') or 'attachment'}]\n{a.get('extracted_content') or ''}"
        for a in atts if isinstance(a, dict)) or None
    files = x.get("files") or []
    file_names = ", ".join(str(f.get("file_name") or f.get("file_uuid") or "") for f in files
                           if isinstance(f, dict)) or None
    msg = {
        "message_id": d["message_id"], "conversation_id": d["conversation_id"], "parent_id": d.get("parent_id"),
        "role": d.get("role"), "message_index": x.get("message_index"), "ts_utc": iso_utc(d.get("created_at")),
        "local_ts": None, "tz": None, "model": x.get("model"), "truncated": x.get("truncated"),
        "text": redact(d.get("text"), counts), "attachments": _cap(redact(att_txt, counts), attach_cap),
        "files": redact(file_names, counts, entropy=False), "src_file": src,
    }
    conv = {
        "conversation_id": d["conversation_id"], "name": redact(d.get("conversation_name"), counts),
        "created_utc": iso_utc(x.get("conversation_created_at")),
        "updated_utc": iso_utc(x.get("conversation_updated_at")), "local_created_ts": None, "tz": None,
        "platform": x.get("platform"), "is_starred": x.get("is_starred"),
        "summary": x.get("conversation_summary"), "src_file": src,
    }
    return msg, conv


class ClaudeAiSource(Source):
    name = "claude_ai"

    def discover(self) -> list[Item]:
        paths = sorted(self.cfg.claude_export_dir.glob("claude_all_transcripts*.parquet"))
        return [Item(key=str(p), path=p) for p in paths]

    def extract(self, items) -> Iterator[Extracted]:
        for it, wm in items:
            src = str(it.path)
            t = pq.read_table(src, columns=["source", "conversation_id", "conversation_name", "message_id",
                                            "parent_id", "role", "created_at", "text", "extra_json"])
            t = t.filter(pc.equal(t["source"], "claude.ai"))
            counts: Counter = Counter()
            msgs, convs = [], {}
            for d in t.to_pylist():
                m, c = parse_rows(d, src, counts, self.cfg.attach_cap)
                msgs.append(m)
                if c["conversation_id"] not in convs:
                    convs[c["conversation_id"]] = c
            for c in convs.values():  # one summary per conversation, redacted once
                c["summary"] = redact(c["summary"], counts)
            yield Extracted(it, db.sha256_file(it.path), wm,
                            {"claude_ai_messages": msgs, "claude_ai_conversations": list(convs.values())},
                            counts, len(msgs))

    def load(self, rows) -> int:
        n = db.insert_staged(self.con, "claude_ai_messages", rows.get("claude_ai_messages", []), MSG, ["message_id"],
                             select="s.* EXCLUDE (local_ts, tz), timezone(p.tz, s.ts_utc) AS local_ts, p.tz AS tz")
        n += db.insert_staged(
            self.con, "claude_ai_conversations", rows.get("claude_ai_conversations", []), CONV, ["conversation_id"],
            select="s.* EXCLUDE (local_created_ts, tz), timezone(p.tz, s.created_utc) AS local_created_ts, p.tz AS tz",
            ts_col="created_utc")
        return n
