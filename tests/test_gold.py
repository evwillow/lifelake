"""The gold set is well formed, answerable through the guard, and scored correctly by the harness."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lifelake.query import SafeLake

REPO = Path(__file__).resolve().parent.parent
GOLD = [json.loads(line) for line in (REPO / "eval" / "gold.jsonl").read_text().splitlines() if line.strip()]


def load_harness():
    spec = importlib.util.spec_from_file_location("eval_run", REPO / "eval" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_gold_shape():
    assert len(GOLD) == 30
    assert len({g["id"] for g in GOLD}) == 30
    assert len({g["question"].lower() for g in GOLD}) == 30
    assert {g["source"] for g in GOLD} == {"claude_ai", "claude_code", "spotify", "takeout", "lake_meta", "cross"}
    assert sum(g["heldout"] for g in GOLD) == 9


@pytest.mark.parametrize("g", GOLD, ids=[g["id"] for g in GOLD])
def test_gold_sql_runs_through_the_guard(demo, g):
    if not demo.prebuilt:
        pytest.skip("gold answers are defined on the full-scale demo; run make demo")
    with SafeLake(demo.db) as lake:
        _, rows = lake.run_sql(g["gold_sql"])
    assert rows, "a gold question with no rows tests nothing"
    assert all(v is not None for r in rows for v in r)


def test_no_tie_at_the_top_n_cut_off(demo):
    if not demo.prebuilt:
        pytest.skip("gold answers are defined on the full-scale demo; run make demo")
    with SafeLake(demo.db) as lake:
        for g in GOLD:
            sql = g["gold_sql"]
            if " limit " not in sql:
                continue
            _, top = lake.run_sql(sql)
            _, every = lake.run_sql(sql.rsplit(" limit ", 1)[0])
            if len(every) > len(top):
                assert every[len(top)][-1] != top[-1][-1], g["id"]


def test_rows_match():
    h = load_harness()
    assert h.rows_match([("a", 1), ("b", 2)], [("b", 2.0), ("a", 1)])
    assert h.rows_match([("a", 1)], [(1, "a")])  # column order is ignored
    assert h.rows_match([(1.0000001,)], [(1.0000002,)])
    assert not h.rows_match([("a", 1)], [("a", 2)])
    assert not h.rows_match([("a", 1)], [("a", 1), ("a", 1)])
    assert not h.rows_match([("a", 1)], [("a", 1, 1)])


def test_parse_reply():
    h = load_harness()
    assert h.parse_reply("```sql\nselect 1\n```") == ("sql", "select 1")
    assert h.parse_reply("REFUSE: no")[0] == "refuse"


def test_oracle_scores_full_marks(demo):
    if not demo.prebuilt:
        pytest.skip("gold answers are defined on the full-scale demo; run make demo")
    p = subprocess.run([sys.executable, str(REPO / "eval" / "run.py"), "--oracle", "--db", str(demo.db), "--tag", "test"],
                       capture_output=True, text=True, cwd=REPO, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "overall 30/30" in p.stdout
    assert "rejected 3/3" in p.stdout
