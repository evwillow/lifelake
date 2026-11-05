"""The generator: determinism, independence from the machine it runs on, ground truth, planted secrets."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import lifelake.synth
from lifelake.redact import find_secrets
from lifelake.synth import PARAMS, generate

SMALL = 0.02
ALLOWED_HOSTS = ("example.com", "example.org", "example.net", "wikipedia.org", "google.com", "youtube.com")
ALLOWED_MAIL = ("example.com", "example.org", "example.net")


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """Relative path -> (content hash, mtime in ns), for every file and folder."""
    out = {}
    for p in sorted(root.rglob("*")):
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
        out[p.relative_to(root).as_posix()] = (digest, p.stat().st_mtime_ns)
    return out


def decoded_text(root: Path) -> str:
    """All text in a tree, including file names and parquet values, with calendar lines unfolded."""
    parts = []
    for p in sorted(root.rglob("*")):
        parts.append(p.relative_to(root).as_posix())
        if not p.is_file():
            continue
        if p.suffix == ".parquet":
            for row in pq.read_table(p).to_pylist():
                parts += [str(v) for v in row.values() if v is not None]
        else:
            parts.append(p.read_text(encoding="utf-8").replace("\r\n ", ""))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    root = tmp_path_factory.mktemp("synth") / "tree"
    secrets: list[str] = []
    truth = generate(root, seed=7, scale=SMALL, secrets_out=secrets)
    return root, truth, secrets


# --- determinism -----------------------------------------------------------------------------

def test_same_seed_gives_identical_tree(small, tmp_path):
    root, truth, _ = small
    again = generate(tmp_path / "again", seed=7, scale=SMALL)
    assert snapshot(root) == snapshot(tmp_path / "again")
    assert truth == again


def test_different_seed_differs(small, tmp_path):
    root, _, _ = small
    generate(tmp_path / "other", seed=8, scale=SMALL)
    assert snapshot(root) != snapshot(tmp_path / "other")


def test_no_dependence_on_environment(small, tmp_path):
    """A second process with another time zone, locale, hash seed and working folder writes the same bytes."""
    root, _, _ = small
    env = {**os.environ, "TZ": "Asia/Tokyo", "PYTHONHASHSEED": "12345", "LC_ALL": "C", "LANG": "C",
           "SOURCE_DATE_EPOCH": "1", "LIFELAKE_DEFAULT_TZ": "Asia/Tokyo"}
    out = tmp_path / "elsewhere" / "tree"
    work = tmp_path / "cwd"
    work.mkdir()
    subprocess.run([sys.executable, "-m", "lifelake.synth", "--out", str(out), "--seed", "7", "--scale", str(SMALL)],
                   check=True, env=env, cwd=work, capture_output=True)
    assert snapshot(root) == snapshot(out)


def test_later_run_gives_same_tree(small, tmp_path):
    """The wall clock moves between runs and the tree does not."""
    root, _, _ = small
    time.sleep(1.1)
    generate(tmp_path / "later", seed=7, scale=SMALL)
    assert snapshot(root) == snapshot(tmp_path / "later")


def test_wall_clock_is_never_read(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the generator read the clock")

    monkeypatch.setattr(time, "time", boom)
    monkeypatch.setattr(time, "time_ns", boom)
    generate(tmp_path / "t", seed=7, scale=SMALL)


def test_source_has_no_clock_or_global_random():
    """Static check of the rules no runtime test can fully prove."""
    banned_attrs = {"now", "utcnow", "today", "uuid1", "uuid4", "urandom", "time_ns", "listdir", "scandir", "walk"}
    for path in sorted(Path(lifelake.synth.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_attrs, f"{path.name}:{node.lineno} uses .{node.attr}"
                if isinstance(node.value, ast.Name) and node.value.id == "random":
                    assert node.attr == "Random", f"{path.name}:{node.lineno} uses global random.{node.attr}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not {"time", "secrets"} & set(names), f"{path.name}:{node.lineno} imports {names}"


def test_params_block_names_each_distribution():
    src = (Path(lifelake.synth.__file__).parent / "params.py").read_text()
    for word in ("Poisson", "mixture", "lognormal", "Zipf", "Bernoulli", "categorical"):
        assert word in src
    assert isinstance(PARAMS, dict) and PARAMS["hour_mixture"]


def test_non_empty_output_folder_is_refused(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        generate(tmp_path, seed=7, scale=SMALL)
    assert (tmp_path / "keep.txt").exists()


def test_overwrite_replaces_only_source_folders(tmp_path):
    generate(tmp_path, seed=7, scale=SMALL)
    (tmp_path / "keep.txt").write_text("x")
    generate(tmp_path, seed=7, scale=SMALL, overwrite=True)
    assert (tmp_path / "keep.txt").exists()


# --- output tree -----------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "claude_export/claude_all_transcripts_demo.parquet", "claude_export/claude_code_transcripts_demo.parquet",
    "takeout/Takeout/My Activity/Search/MyActivity.json", "takeout/Takeout/My Activity/YouTube/MyActivity.json",
    "takeout/Takeout/My Activity/Maps/MyActivity.html", "takeout/Takeout/Chrome/History.json",
    "takeout/Takeout/Location History/Records.json", "spotify/StreamingHistory0.json",
    "spotify/Streaming_History_Audio_2024_0.json", "spotify/SearchQueries.json", "spotify/Playlist1.json",
    "spotify/Inferences.json", "spotify/YourLibrary.json", "spotify/Userdata.json",
])
def test_expected_file_exists(small, rel):
    assert (small[0] / rel).is_file()


def test_calendars_mail_and_transcripts_exist(small):
    root = small[0]
    assert list((root / "takeout/Takeout/Calendar").glob("*.ics"))
    assert list((root / "takeout/Takeout/Mail").glob("*.mbox"))
    assert list((root / "claude_projects").glob("*/*.jsonl"))
    assert list((root / "claude_projects").glob("*/*/subagents/agent-*.jsonl"))


def test_every_mtime_is_in_2024(small):
    for rel, (_h, ns) in snapshot(small[0]).items():
        ts = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        assert ts.year == 2024, rel


def test_mtime_is_time_of_last_record(small):
    root = small[0]
    p = root / "takeout/Takeout/My Activity/Search/MyActivity.json"
    last = max(r["time"] for r in json.loads(p.read_text()))
    assert _mtime_iso(p) == last
    jl = sorted((root / "claude_projects").glob("*/*.jsonl"))[0]
    recs = [json.loads(x) for x in jl.read_text().splitlines() if x.endswith("}")]
    assert _mtime_iso(jl) == max(r["timestamp"] for r in recs if "timestamp" in r)


def _mtime_iso(p: Path) -> str:
    ns = p.stat().st_mtime_ns
    t = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ns // 1000)
    return f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03}Z"


def test_spotify_files_share_no_play(small):
    root = small[0] / "spotify"
    extended = json.loads((root / "Streaming_History_Audio_2024_0.json").read_text())
    ext = {r["ts"][:16].replace("T", " ") for r in extended}
    acc = {r["endTime"] for r in json.loads((root / "StreamingHistory0.json").read_text())}
    assert not ext & acc


def test_overlapping_sessions_add_lines_beyond_the_export(small):
    root, truth, _ = small
    assert truth["cc_live_files"] == len(list((root / "claude_projects").rglob("*.jsonl")))
    assert truth["cc_live_rows"] > 0


# --- ground truth ----------------------------------------------------------------------------

def test_ground_truth_keys(small):
    truth = small[1]
    for key in ("ai_conversations", "ai_messages", "ai_human_messages", "cc_sessions", "cc_messages",
                "cc_real_prompts", "cc_output_tokens", "takeout_search", "takeout_youtube", "takeout_maps",
                "takeout_chrome",
                "takeout_location", "takeout_calendar", "takeout_email", "spotify_plays", "spotify_ms_played",
                "spotify_skipped", "spotify_searches", "spotify_playlists", "spotify_inferences", "spotify_library",
                "secrets_planted", "events_total"):
        assert isinstance(truth[key], int) and truth[key] >= 0, key
    assert truth["cc_sessions"] == (truth["cc_export_only_sessions"] + truth["cc_overlap_sessions"]
                                    + truth["cc_live_only_sessions"])
    assert truth["spotify_plays"] == truth["spotify_plays_extended"] + truth["spotify_plays_account"]
    planted = truth["secrets_planted"]
    assert planted == sum(truth["secrets_by_type"].values()) == sum(truth["secrets_by_source"].values())
    json.dumps(truth)  # plain JSON, so the CLI can print it


def test_default_size_and_scale(tmp_path):
    full = generate(tmp_path / "full", seed=7)
    assert 120 <= full["ai_conversations"] <= 180
    assert 340 <= full["cc_sessions"] <= 460
    assert 17_000 <= full["events_total"] <= 25_000
    half = generate(tmp_path / "half", seed=7, scale=0.5)
    assert 0.35 * full["spotify_plays"] < half["spotify_plays"] < 0.65 * full["spotify_plays"]
    assert 0.35 * full["cc_sessions"] < half["cc_sessions"] < 0.65 * full["cc_sessions"]


def test_scale_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        generate(tmp_path / "x", scale=0)


# --- persona ---------------------------------------------------------------------------------

def test_no_personal_terms_in_output(small):
    """Output holds only the invented persona: allow-lists catch any address, host or home folder that slips in.

    Set LIFELAKE_DENY_TERMS (comma separated, kept out of the repo) to also search for specific words.
    """
    text = decoded_text(small[0])
    mail = set(re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text))
    assert mail and all(d.endswith(ALLOWED_MAIL) for d in mail), sorted(mail)
    hosts = set(re.findall(r"\w+://(?:[^/\s\"'<>@\\]*@)?([^/:?#\s\"'<>\\]+)", text))
    assert hosts and all(h.endswith(ALLOWED_HOSTS) for h in hosts), sorted(hosts)
    assert set(re.findall(r"/home/(\w+)", text)) == {"sam"}
    assert set(re.findall(r"-home-([a-z]+)-", text)) == {"sam"}
    assert "/mnt/" not in text and "/Users/" not in text and "C:\\" not in text
    low = text.lower()
    denied = [t.strip().lower() for t in os.environ.get("LIFELAKE_DENY_TERMS", "").split(",") if t.strip()]
    assert [t for t in denied if t in low] == []


def test_random_strings_cannot_spell_words():
    from lifelake.synth.core import ALNUM, LOWER_ALNUM, UPPER_ALNUM
    for alphabet in (ALNUM, LOWER_ALNUM, UPPER_ALNUM):
        assert not set(alphabet) & set("aeiouAEIOU")


# --- fake secrets ----------------------------------------------------------------------------

def test_fake_secrets_exist_in_raw_output(small):
    root, truth, secrets = small
    assert truth["secrets_planted"] == len(secrets) > 0
    text = decoded_text(root)
    assert [s for s in secrets if s not in text] == []
    found = find_secrets(text, entropy=True)
    assert sum(found.values()) >= truth["secrets_planted"]


def test_secrets_span_every_source_and_several_types(small):
    truth = small[1]
    assert set(truth["secrets_by_source"]) == {"claude_ai", "claude_code", "takeout", "spotify"}
    assert len(truth["secrets_by_type"]) >= 8


def test_each_kind_is_recognized_by_the_redactor():
    import random

    from lifelake.synth.core import KINDS
    rng = random.Random(1)
    for kind, (maker, phrases) in KINDS.items():
        for phrase in phrases:
            found = find_secrets("note " + phrase.format(s=maker(rng)) + " end", entropy=True)
            assert found[kind] >= 1, f"{kind} not caught in {phrase!r}"


def test_secret_rates_are_small():
    assert all(r <= 0.1 for r in PARAMS["secret_rates"].values())
