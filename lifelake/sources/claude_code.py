"""Claude Code transcripts: the parquet export plus live project jsonl files.

Both inputs are parsed from the raw jsonl record with one function, so the columns mean
the same thing whichever file a row came from. The export loads first, then live rows are
anti-joined on (session_id, uuid). Records without a uuid (mode, last-prompt, ai-title, ...)
are bookkeeping and are not stored.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from .. import db
from ..redact import redact
from .base import Extracted, Item, Source, chunks, iso_utc

TS = pa.timestamp("us", tz="UTC")
SCHEMA = pa.schema([
    ("session_id", pa.string()), ("uuid", pa.string()), ("parent_uuid", pa.string()),
    ("root_session_id", pa.string()), ("agent_id", pa.string()), ("workflow_id", pa.string()),
    ("transcript_kind", pa.string()), ("project_key", pa.string()), ("rel_path", pa.string()),
    ("line_no", pa.int64()), ("type", pa.string()), ("subtype", pa.string()), ("role", pa.string()),
    ("ts_utc", TS), ("local_ts", pa.timestamp("us")), ("tz", pa.string()), ("cwd", pa.string()),
    ("git_branch", pa.string()), ("cli_version", pa.string()), ("model", pa.string()),
    ("entrypoint", pa.string()), ("user_type", pa.string()), ("is_sidechain", pa.bool_()),
    ("is_meta", pa.bool_()), ("prompt_source", pa.string()), ("origin_kind", pa.string()),
    ("prompt_id", pa.string()), ("request_id", pa.string()), ("is_real_prompt", pa.bool_()),
    ("tool_names", pa.string()), ("text", pa.string()), ("thinking", pa.string()),
    ("tool_input", pa.string()), ("tool_result", pa.string()), ("input_tokens", pa.int64()),
    ("output_tokens", pa.int64()), ("cache_read_input_tokens", pa.int64()),
    ("cache_creation_input_tokens", pa.int64()), ("stop_reason", pa.string()), ("origin", pa.string()),
    ("src_file", pa.string()),
])

# promptSource values that mean a person typed (or queued, or accepted) the prompt.
HUMAN_SOURCES = {"typed", "queued", "suggestion_accepted"}
# For records written before promptSource existed: harness text that is not a prompt.
NOT_PROMPT_PREFIXES = (
    "<command-name>", "<command-message>", "<local-command-", "<bash-input>", "<bash-stdout>",
    "<bash-stderr>", "<task-notification>", "<system-reminder>", "[Request interrupted",
    "This session is being continued from a previous", "Caveat: The messages below",
    "<<autonomous-loop", "[Image: source:", "Base directory for this skill", "/compact",
    "Continue from where you left off.", "[Your previous response had no visible output",
)
_WF = re.compile(r"/(wf_[A-Za-z0-9_\-]+)/")
_AGENT = re.compile(r"agent-([A-Za-z0-9]+)\.jsonl$")


def path_info(rel: str) -> dict:
    """project_key, root_session_id, transcript_kind, workflow_id, agent_id from a relative path."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    kind = "workflow-agent" if "/subagents/workflows/" in rel else "subagent" if "/subagents/" in rel else "main"
    root = parts[1] if len(parts) > 2 else Path(parts[-1]).stem
    wf = _WF.search(rel)
    ag = _AGENT.search(rel)
    return {"project_key": parts[0] if len(parts) > 1 else None, "root_session_id": root,
            "transcript_kind": kind, "workflow_id": wf.group(1) if wf else None,
            "agent_id": ag.group(1) if ag else None}


def _cap(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else s[:n] + f"…[+{len(s) - n} chars]"


def _block_text(c) -> str:
    """Text of a tool_result content value (string or list of blocks); images become a tag."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    out = []
    for b in c if isinstance(c, list) else [c]:
        if isinstance(b, dict):
            t = b.get("type")
            if t == "text":
                out.append(b.get("text") or "")
            elif t in ("image", "document"):
                out.append(f"[{t}]")
            else:
                out.append(json.dumps(b, ensure_ascii=False)[:2000])
        else:
            out.append(str(b))
    return "\n".join(out)


def is_real_prompt(rec: dict, kind: str, text: str | None, has_tool_result: bool) -> bool:
    if rec.get("type") != "user" or kind != "main" or rec.get("isSidechain") or rec.get("isMeta"):
        return False
    if has_tool_result or not text or not text.strip():
        return False
    ps = rec.get("promptSource")
    ok = (rec.get("origin") or {}).get("kind") if isinstance(rec.get("origin"), dict) else None
    if ps is not None or ok is not None:
        return ps in HUMAN_SOURCES and ok in (None, "human")
    return not text.lstrip().startswith(NOT_PROMPT_PREFIXES)


def parse_record(rec: dict, rel: str, line_no: int, origin: str, src_file: str,
                 counts: Counter, cfg_caps=(8000, 20000)) -> dict | None:
    u = rec.get("uuid")
    if not u:
        return None
    tool_cap, attach_cap = cfg_caps
    pi = path_info(rel)
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content") if msg else rec.get("content")
    texts, thinking, tin, tout, names = [], [], [], [], []
    has_tr = False
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                texts.append(b.get("text") or "")
            elif t == "thinking":
                thinking.append(b.get("thinking") or "")
            elif t in ("tool_use", "server_tool_use", "mcp_tool_use"):
                names.append(b.get("name") or "?")
                tin.append(f"{b.get('name')}: {json.dumps(b.get('input'), ensure_ascii=False)}")
            elif t in ("tool_result", "web_search_tool_result", "mcp_tool_result"):
                has_tr = True
                tout.append(_block_text(b.get("content")))
            elif t in ("image", "document"):
                texts.append(f"[{t}]")
    elif rec.get("type") == "attachment" and rec.get("attachment") is not None:
        texts.append(_cap(redact(json.dumps(rec.get("attachment"), ensure_ascii=False), counts), attach_cap))
    if not texts and isinstance(rec.get("summary"), str):
        texts.append(rec["summary"])
    raw_text = "\n".join(texts) if texts else None
    kind = pi["transcript_kind"]
    real = is_real_prompt(rec, kind, raw_text, has_tr)
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
    origin_d = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    agent = rec.get("agentId") or pi["agent_id"]
    return {
        "session_id": rec.get("sessionId") or pi["root_session_id"], "uuid": u,
        "parent_uuid": rec.get("parentUuid"), "root_session_id": pi["root_session_id"], "agent_id": agent,
        "workflow_id": pi["workflow_id"], "transcript_kind": kind, "project_key": pi["project_key"],
        "rel_path": rel, "line_no": line_no, "type": rec.get("type"), "subtype": rec.get("subtype"),
        "role": msg.get("role"), "ts_utc": iso_utc(rec.get("timestamp")), "local_ts": None, "tz": None,
        "cwd": redact(rec.get("cwd"), counts, entropy=False),
        "git_branch": redact(rec.get("gitBranch"), counts, entropy=False),
        "cli_version": rec.get("version"), "model": msg.get("model"), "entrypoint": rec.get("entrypoint"),
        "user_type": rec.get("userType"), "is_sidechain": rec.get("isSidechain"), "is_meta": rec.get("isMeta"),
        "prompt_source": rec.get("promptSource"), "origin_kind": origin_d.get("kind"),
        "prompt_id": rec.get("promptId"), "request_id": rec.get("requestId"), "is_real_prompt": real,
        "tool_names": ",".join(names) or None,
        # Redact before capping: a cap inside a token would leave a partial secret no pattern can match.
        "text": _cap(redact(raw_text, counts), 200_000),
        "thinking": _cap(redact("\n".join(thinking), counts), 200_000) if thinking else None,
        "tool_input": _cap(redact("\n".join(tin), counts), tool_cap) if tin else None,
        "tool_result": _cap(redact("\n".join(tout), counts), tool_cap) if tout else None,
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "stop_reason": msg.get("stop_reason"), "origin": origin, "src_file": src_file,
    }


# --- workers (top level so they pickle) -------------------------------------------------

def _work_jsonl(args) -> tuple[str, str, str, list[dict], Counter, int, str]:
    path, rel, wm, caps = args
    counts: Counter = Counter()
    rows, bad = [], 0
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        data = f.read()
    h.update(data)
    for i, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            r = parse_record(rec, rel, i, "live", path, counts, caps)
            if r:
                rows.append(r)
    return path, h.hexdigest(), wm, rows, counts, bad, rel


def _work_export_batch(args) -> tuple[list[dict], Counter]:
    records, src, caps = args
    counts: Counter = Counter()
    rows = []
    for raw, rel, line_no in records:
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        r = parse_record(rec, rel, line_no, "export", src, counts, caps)
        if r:
            rows.append(r)
    return rows, counts


class ClaudeCodeSource(Source):
    name = "claude_code"

    def discover(self) -> list[Item]:
        items = [Item(key=str(p), path=p, meta={"kind": "export"})
                 for p in sorted(self.cfg.claude_export_dir.glob("claude_code_transcripts*.parquet"))]
        root = self.cfg.claude_projects
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.endswith(".jsonl"):
                    p = Path(dirpath) / fn
                    items.append(Item(key=str(p), path=p, meta={"kind": "live", "rel": str(p.relative_to(root))}))
        return items

    def watermark(self, item: Item) -> str | None:
        try:
            st = item.path.stat()
        except FileNotFoundError:
            return None
        if item.meta["kind"] == "live":
            if self.cfg.exclude_session and self.cfg.exclude_session in item.meta["rel"]:
                return None  # this session's own transcript (and its subagents)
            if time.time() - st.st_mtime < self.cfg.recent_seconds:
                return None  # still being written
        return f"{st.st_size}:{st.st_mtime_ns}"

    def extract(self, items) -> Iterator[Extracted]:
        caps = (self.cfg.tool_cap, self.cfg.attach_cap)
        exports = [(it, wm) for it, wm in items if it.meta["kind"] == "export"]
        live = [(it, wm) for it, wm in items if it.meta["kind"] == "live"]
        with ProcessPoolExecutor(max_workers=self.cfg.workers, mp_context=mp.get_context("forkserver")) as pool:
            for it, wm in exports:
                yield self._export(pool, it, wm, caps)
            by_key = {str(it.path): it for it, _ in live}
            args = [(str(it.path), it.meta["rel"], wm, caps) for it, wm in live]
            # biggest files first so the pool stays busy
            args.sort(key=lambda a: -int(a[2].split(":")[0]))
            for path, sha, wm, rows, counts, bad, _rel in pool.map(_work_jsonl, args, chunksize=4):
                yield Extracted(by_key[path], sha, wm, {"claude_code_messages": rows}, counts, len(rows),
                                note=f"bad_lines={bad}" if bad else "")

    def _export(self, pool, it: Item, wm: str, caps) -> Extracted:
        src = str(it.path)
        pf = pq.ParquetFile(src)
        cols = ["raw_json", "transcript_rel_path", "line_no"]

        def batches():
            for b in pf.iter_batches(batch_size=4000, columns=cols):
                d = b.to_pydict()
                yield (list(zip(d["raw_json"], d["transcript_rel_path"], d["line_no"], strict=True)), src, caps)

        rows, counts = [], Counter()
        for rs, c in pool.map(_work_export_batch, batches()):
            rows.extend(rs)
            counts.update(c)
        self.log(f"  claude_code: export {it.path.name}: {len(rows)} rows parsed")
        return Extracted(it, db.sha256_file(it.path), wm, {"claude_code_messages": rows}, counts, len(rows))

    def load(self, rows) -> int:
        rs = rows.get("claude_code_messages", [])
        n = 0
        for part in chunks(rs, 100_000):
            n += db.insert_staged(
                self.con, "claude_code_messages", part, SCHEMA, ["session_id", "uuid"],
                select="s.* EXCLUDE (local_ts, tz), timezone(p.tz, s.ts_utc) AS local_ts, p.tz AS tz",
                order="CASE origin WHEN 'export' THEN 0 ELSE 1 END, line_no")
        return n
