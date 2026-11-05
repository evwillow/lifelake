"""Scan every VARCHAR column of every lake table with the regex set. Reports counts only, never values."""
from __future__ import annotations

import multiprocessing as mp
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from .redact import find_secrets

# Bookkeeping tables hold paths and hashes, not content; they are scanned too.
BATCH = 20_000


def _scan_batch(values: list[str]) -> Counter:
    c: Counter = Counter()
    for v in values:
        if v:
            c.update(find_secrets(v))
    return c


def text_columns(con) -> list[tuple[str, str]]:
    return con.execute("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'main' AND data_type = 'VARCHAR' ORDER BY table_name, ordinal_position
    """).fetchall()


def scan_lake(con, workers: int = 8) -> dict:
    by_type: Counter = Counter()
    by_col: Counter = Counter()
    n_values = 0
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("forkserver")) as pool:
        for table, col in text_columns(con):
            cur = con.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')

            def batches():
                while rows := cur.fetchmany(BATCH):
                    yield [r[0] for r in rows]

            for c in pool.map(_scan_batch, batches()):
                if c:
                    by_type.update(c)
                    by_col[f"{table}.{col}"] += sum(c.values())
            n_values += con.execute(f'SELECT count("{col}") FROM "{table}"').fetchone()[0]
    return {"values_scanned": n_values, "hits": sum(by_type.values()), "by_type": dict(by_type),
            "by_column": dict(by_col)}
