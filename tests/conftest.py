"""A demo lake shared by the end-to-end tests.

If ./demo.duckdb exists (from ``make demo``) the tests read it, and regenerate seed 7 at scale 1.0 in a temporary
folder to get its ground truth. A ./demo.duckdb built by older generator code therefore fails loudly: run
``make demo`` again. With no ./demo.duckdb, a demo is built in a temporary folder at scale 0.2.
Nothing here writes to ./demo.duckdb or ./demo_data.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lifelake import db
from lifelake.cli import SOURCES, cmd_refresh
from lifelake.config import Config, parse_travel
from lifelake.synth import DEMO_TRAVEL, DEMO_TZ, generate

REPO = Path(__file__).resolve().parent.parent


def demo_config(db_path: Path, data: Path) -> Config:
    return Config(db_path=db_path, claude_export_dir=data / "claude_export", claude_projects=data / "claude_projects",
                  takeout_dir=data / "takeout", spotify_dir=data / "spotify", default_tz=DEMO_TZ,
                  travel=parse_travel(DEMO_TRAVEL), recent_seconds=0, exclude_session=None, workers=2)


@dataclass(frozen=True)
class Demo:
    db: Path
    cfg: Config          # paths as the build saw them; relative ones resolve from the repo root
    truth: dict
    secrets: list[str]   # planted values, from the same seed and scale as the lake
    prebuilt: bool       # True when reading ./demo.duckdb

    def rows(self, sql: str, *params) -> list[tuple]:
        con = db.connect(self.db, read_only=True)
        try:
            return con.execute(sql, list(params)).fetchall()
        finally:
            con.close()

    def one(self, sql: str, *params):
        return self.rows(sql, *params)[0][0]

    def lake_text(self) -> str:
        """Every text value in every table, as one string."""
        con = db.connect(self.db, read_only=True)
        try:
            cols = con.execute("SELECT table_name, column_name FROM information_schema.columns "
                               "WHERE table_schema = 'main' AND data_type = 'VARCHAR'").fetchall()
            parts = []
            for table, col in cols:
                parts += [r[0] for r in con.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')
                          .fetchall()]
            return "\n".join(parts)
        finally:
            con.close()


@pytest.fixture(scope="session")
def demo(tmp_path_factory) -> Demo:
    tmp = tmp_path_factory.mktemp("demo")
    secrets: list[str] = []
    built = REPO / "demo.duckdb"
    if built.exists():
        truth = generate(tmp / "data", seed=7, scale=1.0, secrets_out=secrets)
        # The relative paths match the manifest keys of a lake built by the Makefile.
        data = Path("demo_data") if (REPO / "demo_data").is_dir() else tmp / "data"
        return Demo(built, demo_config(built, data), truth, secrets, True)
    truth = generate(tmp / "data", seed=7, scale=0.2, secrets_out=secrets)
    cfg = demo_config(tmp / "demo.duckdb", tmp / "data")
    cmd_refresh(cfg, list(SOURCES), write_schema=True)
    return Demo(cfg.db_path, cfg, truth, secrets, False)
