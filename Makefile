# PY defaults to python3 so an activated virtualenv wins. Override with: make PY=/path/to/python demo
PY ?= python3

# CLAUDE_CODE_SESSION_ID is blanked: inside a Claude Code session the connector would skip that session's own
# transcript, and the demo must behave the same everywhere.
DEMO_ENV = LIFELAKE_DB=demo.duckdb \
	LIFELAKE_CLAUDE_EXPORT_DIR=demo_data/claude_export \
	LIFELAKE_CLAUDE_PROJECTS=demo_data/claude_projects \
	LIFELAKE_TAKEOUT_DIR=demo_data/takeout \
	LIFELAKE_SPOTIFY_DIR=demo_data/spotify \
	LIFELAKE_RECENT_SECONDS=0 \
	LIFELAKE_DEFAULT_TZ=America/Chicago \
	LIFELAKE_TRAVEL=Europe/Lisbon:2024-06-03..2024-06-16 \
	CLAUDE_CODE_SESSION_ID=

.PHONY: demo test lint clean

demo:
	rm -rf demo_data
	$(PY) -m lifelake.synth --out demo_data --seed 7 > /dev/null
	rm -f demo.duckdb demo.duckdb.wal
	$(DEMO_ENV) $(PY) -m lifelake.cli refresh
	$(DEMO_ENV) $(PY) -m lifelake.cli scan

test:
	CLAUDE_CODE_SESSION_ID= $(PY) -m pytest

lint:
	$(PY) -m ruff check .

clean:
	rm -rf demo_data demo.duckdb demo.duckdb.wal SCHEMA.md
