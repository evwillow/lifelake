"""Read-only SQL over the safe views.

The lake is attached read-only and queries run only against views in the ``safe`` schema
(``safe_views.sql``), which leave out every free-text column. Anything that is not a single
SELECT/WITH statement, or that touches a raw table, a system table or a table function, is
rejected before it runs.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path

import duckdb

SAFE_SQL = Path(__file__).with_name("safe_views.sql")
TIMEOUT_S = 60
SCRATCH = "scratch"  # throwaway catalog that holds schema `safe`
_ALLOWED_QUAL = {("", ""), ("", "safe"), (SCRATCH, "safe")}

DERIVED = {
    "text_chars": "length of the redacted text; the text itself is not exposed",
    "attachment_chars": "length of extracted attachment text; 0 when none",
    "title_chars": "length of the redacted event title; the title is not exposed",
    "body_chars": "length of the redacted mail body; the body is not exposed",
}


class Rejected(Exception):
    """SQL refused by the guard."""


def safe_views() -> list[str]:
    return sorted(set(re.findall(r"VIEW\s+safe\.(\w+)", SAFE_SQL.read_text(), re.I)))


def _walk(o, tables, funcs, ctes):
    if isinstance(o, dict):
        t = o.get("type")
        if t == "BASE_TABLE":
            tables.append(((o.get("catalog_name") or "").lower(), (o.get("schema_name") or "").lower(),
                           (o.get("table_name") or "").lower()))
        elif t == "TABLE_FUNCTION":
            funcs.append((o.get("function") or {}).get("function_name") or "?")
        cm = o.get("cte_map")
        if isinstance(cm, dict):
            for m in cm.get("map", []) or []:
                k = m.get("key") if isinstance(m, dict) else None
                if k:
                    ctes.add(k.lower())
        for v in o.values():
            _walk(v, tables, funcs, ctes)
    elif isinstance(o, list):
        for v in o:
            _walk(v, tables, funcs, ctes)


class SafeLake:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise SystemExit(f"lake not found: {self.db_path}")
        self._tmp = tempfile.mkdtemp(prefix="lifelake-safe-")
        con = duckdb.connect(":memory:")
        path_sql = str(self.db_path).replace("'", "''")
        con.execute(f"ATTACH '{path_sql}' AS lake (READ_ONLY)")
        con.execute(f"ATTACH '{self._tmp}/safe.duckdb' AS {SCRATCH} (READ_WRITE)")
        con.execute(f"CREATE SCHEMA {SCRATCH}.safe")
        con.execute(f"USE {SCRATCH}.safe")
        con.execute(SAFE_SQL.read_text())
        con.execute("SET TimeZone = 'UTC'")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        self.con = con

    def close(self) -> None:
        self.con.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def validate(self, sql: str) -> str:
        """Return the cleaned SQL or raise Rejected."""
        s = (sql or "").strip().rstrip(";").strip()
        if not s:
            raise Rejected("empty SQL")
        try:
            stmts = self.con.extract_statements(s)
        except duckdb.Error as e:
            raise Rejected(f"parse error: {str(e).splitlines()[0]}") from e
        if len(stmts) != 1:
            raise Rejected(f"expected exactly one statement, got {len(stmts)}")
        if stmts[0].type != duckdb.StatementType.SELECT:
            raise Rejected(f"only SELECT/WITH is allowed, got {stmts[0].type.name}")
        if not re.match(r"^\s*(\(\s*)*(select|with|from)\b", s, re.I):
            raise Rejected("statement must start with SELECT or WITH")
        ast = json.loads(self.con.execute("SELECT json_serialize_sql(?)", [s]).fetchone()[0])
        if ast.get("error"):
            raise Rejected(f"not a plain SELECT: {ast.get('error_message')}")
        tables, funcs, ctes = [], [], set()
        _walk(ast, tables, funcs, ctes)
        if funcs:
            raise Rejected(f"table functions are not allowed: {', '.join(sorted(set(funcs)))}")
        views = set(safe_views())
        for cat, sch, name in tables:
            if (cat, sch) == ("", "") and name in ctes:
                continue
            if name in views and (cat, sch) in _ALLOWED_QUAL:
                continue
            full = ".".join(x for x in (cat, sch, name) if x)
            raise Rejected(f"'{full}' is not a safe view; use safe.<view> ({', '.join(sorted(views))})")
        return s

    def run_sql(self, sql: str, timeout: float = TIMEOUT_S):
        """Validate and run. Returns (columns, rows). Raises Rejected or duckdb.Error."""
        s = self.validate(sql)
        timer = threading.Timer(timeout, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(s)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            timer.cancel()
        return cols, rows

    def context(self) -> str:
        """The safe schema, row counts and 3 sample values per column: all an LLM gets to see."""
        from .schema_doc import COLUMN_NOTES, TABLE_NOTES
        lines = ["# Safe views (query as safe.<view>; nothing else is readable)", ""]
        for v in safe_views():
            n = self.con.execute(f"SELECT count(*) FROM safe.{v}").fetchone()[0]
            lines.append(f"## safe.{v} ({n:,} rows) — {TABLE_NOTES.get(v, '')}")
            for name, typ, *_ in self.con.execute(f"DESCRIBE safe.{v}").fetchall():
                vals = self.con.execute(f'SELECT "{name}" v, count(*) n FROM safe.{v} WHERE "{name}" IS NOT NULL '
                                        f"GROUP BY 1 ORDER BY n DESC, v LIMIT 3").fetchall()
                note = COLUMN_NOTES.get((v, name)) or DERIVED.get(name, "")
                ex = ", ".join(repr(_short(x[0])) for x in vals)
                lines.append(f"- {name} {typ}" + (f" — {note}" if note else "") + f" | e.g. {ex}")
            lines.append("")
        return "\n".join(lines)


def cell(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _short(v, n: int = 60) -> str:
    v = cell(v)
    s = "NULL" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def print_result(sql: str, cols, rows, limit: int = 20) -> None:
    print("SQL:")
    print(sql)
    print(f"\nrows: {len(rows)}" + (f" (showing {limit})" if len(rows) > limit else ""))
    if not rows:
        return
    shown = [[_short(v) for v in r] for r in rows[:limit]]
    w = [max(len(c), *(len(r[i]) for r in shown)) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * x for x in w))
    for r in shown:
        print(" | ".join(v.ljust(w[i]) for i, v in enumerate(r)))
