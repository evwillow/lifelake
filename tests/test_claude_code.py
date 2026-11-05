"""Claude Code connector: record parsing, path info, export + live dedupe, deferral, incremental refresh."""
import json
import os
import time
from collections import Counter

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lifelake.cli import cmd_refresh
from lifelake.config import Config
from lifelake.sources.claude_code import is_real_prompt, parse_record, path_info

SECRET = "ghp_" + "Zq9" * 12
PROJ = "-home-sam-demo"


def rec(**kw):
    base = {"type": "user", "uuid": "u1", "sessionId": "s1", "timestamp": "2024-05-01T10:00:00Z",
            "isSidechain": False, "message": {"role": "user", "content": "fix the bug"}}
    base.update(kw)
    return base


@pytest.mark.parametrize("r,kind,expect", [
    (rec(promptSource="typed", origin={"kind": "human"}), "main", True),
    (rec(promptSource="queued", origin={"kind": "human"}), "main", True),
    (rec(promptSource="system", origin={"kind": "task-notification"}), "main", False),
    (rec(promptSource="sdk"), "main", False),
    (rec(), "main", True),
    (rec(message={"role": "user", "content": "<command-name>/compact</command-name>"}), "main", False),
    (rec(message={"role": "user", "content": "[Request interrupted by user]"}), "main", False),
    (rec(isMeta=True), "main", False),
    (rec(isSidechain=True), "main", False),
    (rec(), "subagent", False),
    (rec(message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}),
     "main", False),
    (rec(type="assistant"), "main", False),
    (rec(message={"role": "user", "content": "   "}), "main", False),
])
def test_is_real_prompt(r, kind, expect):
    rel = f"{PROJ}/s1.jsonl" if kind == "main" else f"{PROJ}/s1/subagents/agent-abc.jsonl"
    row = parse_record(r, rel, 1, "live", "x", Counter())
    assert row["is_real_prompt"] is expect


def test_is_real_prompt_direct():
    assert is_real_prompt(rec(), "main", "hello", False) is True
    assert is_real_prompt(rec(), "main", "hello", True) is False


def test_parse_assistant_blocks_and_redaction():
    r = {"type": "assistant", "uuid": "a1", "sessionId": "s1", "timestamp": "2024-05-01T10:00:01.5Z",
         "message": {"role": "assistant", "model": "claude-test", "usage": {"input_tokens": 3, "output_tokens": 7},
                     "content": [{"type": "thinking", "thinking": "hmm"},
                                 {"type": "text", "text": "here"},
                                 {"type": "tool_use", "name": "Bash",
                                  "input": {"command": "export GH=ghp_" + "a1B2" * 9}}]}}
    c = Counter()
    row = parse_record(r, f"{PROJ}/s1.jsonl", 3, "live", "x", c)
    assert row["tool_names"] == "Bash" and row["thinking"] == "hmm" and row["text"] == "here"
    assert "[REDACTED:github_token]" in row["tool_input"] and "ghp_a1B2" not in row["tool_input"]
    assert c == Counter({"github_token": 1})
    assert row["output_tokens"] == 7 and row["ts_utc"].microsecond == 500000


def test_parse_tool_result_redacted():
    r = rec(uuid="t1", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": "key " + SECRET},
                                                               {"type": "image"}]}]})
    c = Counter()
    row = parse_record(r, f"{PROJ}/s1.jsonl", 1, "live", "x", c)
    assert SECRET not in row["tool_result"] and "[REDACTED:github_token]" in row["tool_result"]
    assert "[image]" in row["tool_result"] and row["is_real_prompt"] is False


def test_no_uuid_records_skipped():
    assert parse_record({"type": "mode", "mode": "x"}, "p/s.jsonl", 1, "live", "x", Counter()) is None


def test_path_info():
    p = path_info(f"{PROJ}/sess/subagents/workflows/wf_abc-1/agent-a1b2.jsonl")
    assert p["transcript_kind"] == "workflow-agent" and p["workflow_id"] == "wf_abc-1"
    assert p["agent_id"] == "a1b2" and p["root_session_id"] == "sess" and p["project_key"] == PROJ
    sub = path_info(f"{PROJ}/sess/subagents/agent-zz9.jsonl")
    assert sub["transcript_kind"] == "subagent" and sub["workflow_id"] is None and sub["agent_id"] == "zz9"
    main = path_info(f"{PROJ}/sess.jsonl")
    assert main["transcript_kind"] == "main" and main["root_session_id"] == "sess"
    assert path_info(PROJ + "\\sess.jsonl")["project_key"] == PROJ


def jl(uuid, session, text, ts="2024-05-10T12:00:00Z", **kw):
    r = {"type": "user", "uuid": uuid, "sessionId": session, "timestamp": ts, "isSidechain": False,
         "promptSource": "typed", "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}
    r.update(kw)
    return json.dumps(r)


@pytest.fixture()
def world(tmp_path):
    exp = tmp_path / "export"
    exp.mkdir()
    proj = tmp_path / "projects" / PROJ
    proj.mkdir(parents=True)
    # export: s1 (u1, u2) and s2 (u3); u1 appears twice inside the export
    lines = [(jl("u1", "s1", "hello"), f"{PROJ}/s1.jsonl", 1),
             (jl("u1", "s1", "hello"), f"{PROJ}/s1.jsonl", 1),
             (jl("u2", "s1", f"push with {SECRET}"), f"{PROJ}/s1.jsonl", 2),
             (jl("u3", "s2", "other"), f"{PROJ}/s2.jsonl", 1)]
    pq.write_table(pa.table({"raw_json": [x[0] for x in lines], "transcript_rel_path": [x[1] for x in lines],
                             "line_no": [x[2] for x in lines]}), exp / "claude_code_transcripts_test.parquet")
    # live: s1 overlaps the export and adds u4; s3 is new; one recent file; one own-session file
    old = time.time() - 7200
    for name, body in [("s1.jsonl", [jl("u1", "s1", "hello"), jl("u2", "s1", f"push with {SECRET}"),
                                     jl("u4", "s1", "new line"), '{"type":"mode","mode":"x"}', "not json"]),
                       ("s3.jsonl", [jl("u5", "s3", "fresh"), jl("u6", "s3", "done", type="assistant")])]:
        p = proj / name
        p.write_text("\n".join(body) + "\n")
        os.utime(p, (old, old))
    (proj / "recent.jsonl").write_text(jl("u7", "recent", "being written") + "\n")
    me = proj / "me-session.jsonl"
    me.write_text(jl("u8", "me-session", "this session") + "\n")
    os.utime(me, (old, old))
    cfg = Config(db_path=tmp_path / "lake.duckdb", claude_export_dir=exp, claude_projects=tmp_path / "projects",
                 recent_seconds=1800, exclude_session="me-session", workers=2)
    return cfg


def test_refresh_dedupe_deferral_and_incremental(world):
    cfg = world
    r1 = cmd_refresh(cfg, ["claude_code"], write_schema=False)
    assert r1["rows_added"] == 6
    r2 = cmd_refresh(cfg, ["claude_code"], write_schema=False)
    assert r2["rows_added"] == 0
    assert all(s.get("files_loaded", 0) == 0 for s in r2["sources"])

    con = duckdb.connect(str(cfg.db_path), read_only=True)
    uu = con.execute("SELECT uuid, origin FROM claude_code_messages ORDER BY uuid").fetchall()
    # export wins the overlap; the recent file and the own session are deferred
    assert uu == [("u1", "export"), ("u2", "export"), ("u3", "export"), ("u4", "live"), ("u5", "live"),
                  ("u6", "live")]
    assert con.execute("SELECT count(*) FROM claude_code_messages WHERE is_real_prompt").fetchone()[0] == 5
    txt = " ".join(r[0] for r in con.execute("SELECT text FROM claude_code_messages").fetchall())
    assert SECRET not in txt and "[REDACTED:github_token]" in txt
    assert con.execute("SELECT count(*) FROM claude_code_messages WHERE tz IS NULL").fetchone()[0] == 0
    red = dict(con.execute("SELECT type, sum(hits) FROM redaction_log GROUP BY 1").fetchall())
    assert red.get("github_token", 0) >= 2
    con.close()

    # an appended line is picked up, and only it
    p = cfg.claude_projects / PROJ / "s3.jsonl"
    with open(p, "a") as f:
        f.write(jl("u9", "s3", "more") + "\n")
    old = time.time() - 7200
    os.utime(p, (old, old))
    r3 = cmd_refresh(cfg, ["claude_code"], write_schema=False)
    assert r3["rows_added"] == 1
    r4 = cmd_refresh(cfg, ["claude_code"], write_schema=False)
    assert r4["rows_added"] == 0
