"""claude.ai and Claude Code: the two export parquet files and the live project transcripts."""
from __future__ import annotations

import json
import random
from collections import Counter
from datetime import date, datetime, timedelta

import pyarrow as pa

from . import text as tx
from .core import (
    HARD_END,
    Planter,
    Tree,
    Zipf,
    categorical,
    in_trip,
    iso_ms,
    lake_zone,
    lognormal,
    moments,
    new_uuid,
    poisson,
    rand_str,
    scaled,
    span,
)
from .params import PARAMS

AI_SCHEMA = pa.schema([(c, pa.string()) for c in (
    "source", "conversation_id", "conversation_name", "message_id", "parent_id", "role", "created_at", "text",
    "extra_json")])
CC_SCHEMA = pa.schema([("raw_json", pa.string()), ("transcript_rel_path", pa.string()), ("line_no", pa.int64())])


def _dumps(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


def _gap(rng: random.Random, params) -> timedelta:
    return timedelta(milliseconds=int(min(1800.0, lognormal(rng, params)) * 1000) + 1)


# --- claude.ai ----------------------------------------------------------------------------

def _ai_conversation(rng: random.Random, plant: Planter, start: datetime) -> tuple[list[dict], dict]:
    cid = new_uuid(rng)
    project = rng.choice(tx.PROJECTS)
    name = plant.field("claude_ai.name", tx.fill(rng, rng.choice(tx.TITLES), project), single_line=True)
    summary = plant.field("claude_ai.summary", tx.fill(rng, rng.choice(tx.SUMMARIES), project))
    model = categorical(rng, PARAMS["ai_models"])
    meta = {"platform": categorical(rng, PARAMS["ai_platforms"]), "is_starred": rng.random() < PARAMS["ai_starred"],
            "conversation_summary": summary, "conversation_created_at": iso_ms(start - timedelta(seconds=5))}
    msgs: list[tuple] = []
    t, parent = start, None
    for i in range(2 * max(1, round(lognormal(rng, PARAMS["ai_turns"])))):
        human = i % 2 == 0
        extra = {"message_index": i}
        if human:
            body = tx.compose(rng, tx.PROMPTS, lognormal(rng, PARAMS["ai_human_chars"]), project)
            body = plant.field("claude_ai.text", body)
            if rng.random() < PARAMS["ai_attachment"]:
                content = tx.compose(rng, tx.REPLIES, 300, project)
                extra["attachments"] = [{"file_name": rng.choice(tx.ATTACHMENT_NAMES), "file_type": "text/plain",
                                         "extracted_content": plant.field("claude_ai.attachment", content)}]
            if rng.random() < PARAMS["ai_file"]:
                fname = plant.field("claude_ai.file_name", rng.choice(tx.UPLOAD_NAMES), single_line=True, entropy=False)
                extra["files"] = [{"file_name": fname, "file_uuid": new_uuid(rng)}]
        else:
            reply = tx.compose(rng, tx.REPLIES, lognormal(rng, PARAMS["ai_assistant_chars"]), project)
            body = plant.field("claude_ai.text", reply)
            extra["model"] = model
            extra["truncated"] = rng.random() < PARAMS["ai_truncated"]
        mid = new_uuid(rng)
        msgs.append((mid, parent, "human" if human else "assistant", t, body, extra))
        parent = mid
        t = t + _gap(rng, PARAMS["ai_gap_seconds"])
        if t >= HARD_END and not human:
            break
    meta["conversation_updated_at"] = iso_ms(msgs[-1][3])
    rows = [{"source": "claude.ai", "conversation_id": cid, "conversation_name": name, "message_id": mid,
             "parent_id": par, "role": role, "created_at": iso_ms(ts), "text": body,
             "extra_json": _dumps({**meta, **extra})} for mid, par, role, ts, body, extra in msgs]
    facts = {"model": model, "starred": meta["is_starred"], "times": [m[3] for m in msgs],
             "human": sum(m[2] == "human" for m in msgs), "assistant": sum(m[2] == "assistant" for m in msgs)}
    return rows, facts


def _write_claude_ai(rng, tree: Tree, plant: Planter, scale: float, code_copies: list[dict]) -> dict:
    rows: list[dict] = []
    models: Counter = Counter()
    times: list[datetime] = []
    humans = assistants = starred = 0
    starts = moments(rng, scaled("ai_conversations", scale))
    for start in starts:
        rs, f = _ai_conversation(rng, plant, start)
        rows.extend(rs)
        times.extend(f["times"])
        humans += f["human"]
        assistants += f["assistant"]
        starred += f["starred"]
        models[f["model"]] += f["assistant"]
    dups = [dict(r) for r in rows if rng.random() < PARAMS["ai_duplicate_row"]]
    table = pa.Table.from_pylist(rows + dups + code_copies, schema=AI_SCHEMA)
    tree.write_parquet("claude_export/claude_all_transcripts_demo.parquet", table, max(times))
    return {"ai_conversations": len(starts), "ai_messages": humans + assistants, "ai_human_messages": humans,
            "ai_assistant_messages": assistants, "ai_starred": starred, "ai_models": dict(sorted(models.items())),
            "ai_duplicate_rows": len(dups), "ai_code_copies_ignored": len(code_copies),
            "ai_ts_range": span(times), "ai_messages_in_trip": sum(in_trip(t.date()) for t in times)}


# --- Claude Code --------------------------------------------------------------------------

class _Session:
    """Builds one transcript: a list of raw jsonl records in file order."""

    def __init__(self, rng, plant: Planter, sid: str, project: str, t: datetime, *, sidechain=False, agent_id=None):
        self.rng, self.plant, self.sid, self.project, self.t = rng, plant, sid, project, t
        self.sidechain, self.agent_id = sidechain, agent_id
        self.lines: list[dict] = []
        self.real: set[str] = set()
        self.parent = None
        self.legacy = rng.random() < PARAMS["cc_legacy_format"]
        self.version = ("1.9." if self.legacy else "2.1.") + str(rng.randrange(40))
        self.branch = categorical(rng, PARAMS["cc_branches"])
        self.cwd = f"{tx.HOME_DIR}/{project}"

    def rec(self, typ: str, **kw) -> dict:
        self.t = self.t + _gap(self.rng, PARAMS["cc_gap_seconds"])
        u = new_uuid(self.rng)
        r = {"type": typ, "uuid": u, "parentUuid": self.parent, "sessionId": self.sid, "timestamp": iso_ms(self.t),
             "cwd": self.cwd, "gitBranch": self.branch, "version": self.version, "entrypoint": "cli",
             "userType": "external", "isSidechain": self.sidechain}
        if self.agent_id:
            r["agentId"] = self.agent_id
        r.update(kw)
        self.lines.append(r)
        self.parent = u
        return r

    def prompt(self) -> None:
        rng = self.rng
        label = "claude_code.subagent" if self.sidechain else "claude_code.prompt"
        body = self.plant.field(label, tx.compose(rng, tx.PROMPTS, lognormal(rng, PARAMS["cc_prompt_chars"]),
                                                  self.project))
        content = body
        if rng.random() < PARAMS["cc_image_prompt"]:
            content = [{"type": "text", "text": body},
                       {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": rand_str(rng, 24)}}]
        kw = {"message": {"role": "user", "content": content}}
        if not self.legacy:
            kw["promptId"] = new_uuid(rng)
            kw["promptSource"] = categorical(rng, PARAMS["cc_prompt_sources"])
            kw["origin"] = {"kind": "human"}
        r = self.rec("user", **kw)
        if not self.sidechain:
            self.real.add(r["uuid"])

    def harness_record(self) -> None:
        """A user record no person typed; each of these must end up with is_real_prompt = false."""
        rng = self.rng
        choice = rng.randrange(5)
        if choice == 0:
            self.rec("user", isMeta=True, message={"role": "user", "content": "Caveat: The messages below were "
                     "generated by the user while running local commands."})
        elif choice == 1:
            self.rec("user", message={"role": "user", "content": "<command-name>/compact</command-name>"})
        elif choice == 2:
            self.rec("user", message={"role": "user", "content": "[Request interrupted by user]"})
        elif choice == 3:
            kw = {} if self.legacy else {"promptSource": "system", "origin": {"kind": "task-notification"}}
            self.rec("user", message={"role": "user", "content": "<task-notification>job finished</task-notification>"},
                     **kw)
        else:
            self.rec("user", message={"role": "user", "content": "This session is being continued from a previous "
                                                                 "conversation that ran out of context."})

    def _tool_input(self, name: str) -> dict:
        rng, cwd = self.rng, self.cwd
        path = f"{cwd}/{rng.choice(tx.FILES)}"
        if name == "Bash":
            label = "claude_code.subagent" if self.sidechain else "claude_code.tool_input"
            return {"command": self.plant.field(label, rng.choice(tx.COMMANDS)), "description": "Run a command"}
        if name == "Read":
            return {"file_path": path}
        if name == "Edit":
            return {"file_path": path, "old_string": rng.choice(tx.FUNCTIONS), "new_string": rng.choice(tx.FUNCTIONS)}
        if name == "Write":
            return {"file_path": path, "content": tx.fill(rng, rng.choice(tx.RESULTS), self.project)}
        if name == "Grep":
            return {"pattern": rng.choice(tx.FUNCTIONS), "path": cwd}
        if name == "Glob":
            return {"pattern": "**/*.py", "path": cwd}
        if name == "WebFetch":
            return {"url": "https://docs.example.com/" + rng.choice(tx.SITE_WORDS), "prompt": "Summarize this page"}
        return {"description": "Look into " + rng.choice(tx.TOPICS), "prompt": tx.fill(rng, rng.choice(tx.PROMPTS))}

    def assistant(self, stop: str, blocks: list[dict]) -> None:
        rng = self.rng
        if rng.random() < PARAMS["cc_thinking"]:
            label = "claude_code.subagent" if self.sidechain else "claude_code.thinking"
            thought = self.plant.field(label, tx.compose(rng, tx.THOUGHTS, lognormal(rng, PARAMS["cc_thinking_chars"]),
                                                         self.project))
            blocks = [{"type": "thinking", "thinking": thought, "signature": rand_str(rng, 16)}] + blocks
        usage = {
            "input_tokens": int(lognormal(rng, PARAMS["cc_input_tokens"])),
            "output_tokens": int(lognormal(rng, PARAMS["cc_output_tokens"])),
            "cache_read_input_tokens": int(lognormal(rng, PARAMS["cc_cache_read_tokens"])),
            "cache_creation_input_tokens": int(lognormal(rng, PARAMS["cc_cache_creation_tokens"]))}
        self.rec("assistant", requestId="req_" + rand_str(rng, 24), message={
            "id": "msg_" + rand_str(rng, 24), "role": "assistant", "model": categorical(rng, PARAMS["cc_models"]),
            "stop_reason": stop, "usage": usage, "content": blocks})

    def tool_loop(self) -> None:
        rng = self.rng
        names = [categorical(rng, PARAMS["cc_tools"])]
        if rng.random() < PARAMS["cc_parallel_tools"]:
            names.append(categorical(rng, PARAMS["cc_tools"]))
        calls = [{"type": "tool_use", "id": "toolu_" + rand_str(rng, 24), "name": n, "input": self._tool_input(n)}
                 for n in names]
        self.assistant("tool_use", list(calls))
        label = "claude_code.subagent" if self.sidechain else "claude_code.tool_result"
        results = []
        for i, c in enumerate(calls):
            body = self.plant.field(label, tx.compose(rng, tx.RESULTS, lognormal(rng, PARAMS["cc_result_chars"]),
                                                      self.project))
            # Results come in both shapes the format allows: a plain string and a list of text blocks.
            content = body if i % 2 == 0 else [{"type": "text", "text": body}]
            results.append({"type": "tool_result", "tool_use_id": c["id"], "content": content})
        self.rec("user", message={"role": "user", "content": results})

    def reply(self) -> None:
        rng = self.rng
        label = "claude_code.subagent" if self.sidechain else "claude_code.reply"
        body = self.plant.field(label, tx.compose(rng, tx.REPLIES, lognormal(rng, PARAMS["cc_reply_chars"]),
                                                  self.project))
        self.assistant("end_turn", [{"type": "text", "text": body}])

    def extras(self) -> None:
        rng = self.rng
        if rng.random() < PARAMS["cc_harness_record"]:
            self.harness_record()
        if rng.random() < PARAMS["cc_attachment_record"]:
            self.rec("attachment", attachment={"type": "file", "path": f"{self.cwd}/{rng.choice(tx.FILES)}",
                                               "content": tx.fill(rng, rng.choice(tx.RESULTS), self.project)})
        if rng.random() < PARAMS["cc_compact_record"]:
            self.rec("system", subtype="compact_boundary", content="Conversation compacted")


def _main_session(rng, plant: Planter, start: datetime, project: str) -> _Session:
    s = _Session(rng, plant, new_uuid(rng), project, start)
    s.lines.append({"type": "mode", "mode": "default", "sessionId": s.sid})
    for _ in range(max(1, round(lognormal(rng, PARAMS["cc_prompts"])))):
        s.prompt()
        for _ in range(poisson(rng, PARAMS["cc_tool_loops"])):
            s.tool_loop()
        s.reply()
        s.extras()
        if s.t >= HARD_END:
            break
    s.lines.append({"type": "ai-title", "aiTitle": tx.fill(rng, rng.choice(tx.TITLES), project), "sessionId": s.sid})
    s.lines.append({"type": "last-prompt", "lastPrompt": "not stored", "sessionId": s.sid})
    return s


def _subagent(rng, plant: Planter, parent: _Session, start: datetime) -> _Session:
    agent = rand_str(rng, 16, "0123456789abcdef")
    s = _Session(rng, plant, parent.sid, parent.project, start, sidechain=True, agent_id=agent)
    s.legacy = parent.legacy
    s.version, s.branch = parent.version, parent.branch
    for _ in range(1 + poisson(rng, PARAMS["cc_subagent_turns"])):
        s.prompt()
        s.tool_loop()
        s.reply()
    return s


def _last_ts(lines: list[dict]) -> datetime:
    return max(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) for r in lines if "timestamp" in r)


def _write_claude_code(rng, tree: Tree, plant: Planter, scale: float) -> tuple[dict, list[dict]]:
    cutoff = date.fromisoformat(PARAMS["cc_export_cutoff"])
    zipf = Zipf(tx.PROJECTS, PARAMS["cc_project_zipf"])
    export: list[tuple[str, str, int]] = []
    records: list[dict] = []       # every record with a uuid, once
    real: set[str] = set()
    per_project: Counter = Counter()
    kinds: Counter = Counter()
    sessions = 0
    live_files = subagent_files = 0
    export_uuids: set[str] = set()
    forced_live_subagent = False
    last_export: datetime | None = None

    starts = moments(rng, scaled("cc_sessions", scale))
    for start in starts:
        project = "-home-sam-projects-" + zipf.draw(rng)
        s = _main_session(rng, plant, start, project.removeprefix("-home-sam-projects-"))
        sessions += 1
        per_project[project] += 1
        real |= s.real
        rel = f"{project}/{s.sid}.jsonl"
        before_cutoff = start.date() < cutoff
        overlap = before_cutoff and rng.random() < PARAMS["cc_overlap"]
        kind = "overlap" if overlap else "export_only" if before_cutoff else "live_only"
        kinds[kind] += 1
        keep = max(1, int(len(s.lines) * PARAMS["cc_overlap_keep"])) if overlap else len(s.lines)
        if kind in ("export_only", "overlap"):
            for i, r in enumerate(s.lines[:keep], 1):
                export.append((_dumps(r), rel, i))
                if "uuid" in r:
                    export_uuids.add(r["uuid"])
                    if rng.random() < PARAMS["cc_duplicate_row"]:
                        export.append((_dumps(r), rel, i))
            last_export = max(filter(None, (last_export, _last_ts(s.lines[:keep]))), default=None)
        if kind in ("overlap", "live_only"):
            body = "\n".join(_dumps(r) for r in s.lines) + "\n"
            if rng.random() < PARAMS["cc_torn_line"]:
                body += '{"type":"assistant","uuid":"torn'
            tree.write(f"claude_projects/{rel}", body, _last_ts(s.lines))
            live_files += 1
        records.extend(r for r in s.lines if "uuid" in r)

        # At least one live subagent file at any scale, so that path is always exercised.
        spawn = rng.random() < PARAMS["cc_subagent"]
        if kind != "export_only" and not forced_live_subagent:
            spawn, forced_live_subagent = True, True
        if not spawn:
            continue
        for _ in range(1 + poisson(rng, PARAMS["cc_subagent_files"])):
            a = _subagent(rng, plant, s, start + timedelta(seconds=rng.randrange(1, 120)))
            sub_rel = f"{project}/{s.sid}/subagents/agent-{a.agent_id}.jsonl"
            records.extend(a.lines)
            if kind == "export_only":
                for i, r in enumerate(a.lines, 1):
                    export.append((_dumps(r), sub_rel, i))
                    export_uuids.add(r["uuid"])
                last_export = max(filter(None, (last_export, _last_ts(a.lines))), default=None)
            else:
                tree.write(f"claude_projects/{sub_rel}", "\n".join(_dumps(r) for r in a.lines) + "\n",
                           _last_ts(a.lines))
                live_files += 1
                subagent_files += 1

    live_rows = sum(r["uuid"] not in export_uuids for r in records)
    # A real export can hold empty and cut-off rows; the connector skips both.
    bad_rows = [(None, "-home-sam-projects-none/none.jsonl", 1),
                ('{"type":"user","uuid":"cut', "-home-sam-projects-none/none.jsonl", 2)]
    table = pa.Table.from_pylist(
        [{"raw_json": raw, "transcript_rel_path": rel, "line_no": n} for raw, rel, n in export + bad_rows],
        schema=CC_SCHEMA)
    tree.write_parquet("claude_export/claude_code_transcripts_demo.parquet", table, last_export or starts[0])

    truth = _cc_truth(records, real, sessions, kinds, per_project, live_files, subagent_files, live_rows)
    # The all-transcripts export also carries a copy of some Claude Code rows, which claude_ai must drop.
    copies = []
    for r in records:
        if len(copies) >= PARAMS["ai_code_copies"]:
            break
        if isinstance((r.get("message") or {}).get("content"), str):
            copies.append({"source": "claude_code", "conversation_id": r["sessionId"], "conversation_name": None,
                           "message_id": r["uuid"], "parent_id": r["parentUuid"], "role": r["type"],
                           "created_at": r["timestamp"], "text": r["message"]["content"], "extra_json": "{}"})
    return truth, copies


def _cc_truth(records, real, sessions, kinds, per_project, live_files, subagent_files, live_rows) -> dict:
    tokens: Counter = Counter()
    tools: Counter = Counter()
    models: Counter = Counter()
    times, prompts_by_hour = [], Counter()
    assistants = subagent_msgs = in_trip_n = 0
    for r in records:
        ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        times.append(ts)
        in_trip_n += in_trip(ts.date())
        subagent_msgs += bool(r.get("isSidechain"))
        if r["uuid"] in real:
            prompts_by_hour[ts.astimezone(lake_zone(ts)).hour] += 1
        if r["type"] == "assistant":
            assistants += 1
            models[r["message"]["model"]] += 1
            for k, v in r["message"]["usage"].items():
                tokens[k] += v
            for b in r["message"]["content"]:
                if b["type"] == "tool_use":
                    tools[b["name"]] += 1
    return {
        "cc_sessions": sessions, "cc_messages": len(records), "cc_real_prompts": len(real),
        "cc_assistant_messages": assistants, "cc_subagent_messages": subagent_msgs,
        "cc_input_tokens": tokens["input_tokens"], "cc_output_tokens": tokens["output_tokens"],
        "cc_cache_read_tokens": tokens["cache_read_input_tokens"],
        "cc_cache_creation_tokens": tokens["cache_creation_input_tokens"],
        "cc_tools": dict(sorted(tools.items())), "cc_models": dict(sorted(models.items())),
        "cc_sessions_by_project": dict(sorted(per_project.items())),
        "cc_export_only_sessions": kinds["export_only"], "cc_overlap_sessions": kinds["overlap"],
        "cc_live_only_sessions": kinds["live_only"], "cc_live_files": live_files,
        "cc_live_subagent_files": subagent_files, "cc_live_rows": live_rows,
        "cc_messages_in_trip": in_trip_n, "cc_ts_range": span(times),
        "cc_prompts_by_local_hour": {str(h): prompts_by_hour[h] for h in sorted(prompts_by_hour)},
    }


def write_claude(rng_ai, rng_cc, tree: Tree, plant: Planter, scale: float) -> dict:
    truth, copies = _write_claude_code(rng_cc, tree, plant, scale)
    truth.update(_write_claude_ai(rng_ai, tree, plant, scale, copies))
    return truth
