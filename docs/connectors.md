# Connectors

A connector turns one kind of export into rows in the lake. It finds its input files, parses the new ones and redacts every text value. The driver in `lifelake/sources/base.py` handles the manifest, batching, transactions and run logging.

## Input formats

### `claude_ai`

Reads `claude_all_transcripts*.parquet` from `LIFELAKE_CLAUDE_EXPORT_DIR` and keeps rows with `source = 'claude.ai'`.

The file needs these columns: `source`, `conversation_id`, `conversation_name`, `message_id`, `parent_id`, `role`, `created_at`, `text`, `extra_json`.

`extra_json` is a JSON object. The connector reads these keys when they are present: `message_index`, `model`, `truncated`, `attachments`, `files`, `conversation_created_at`, `conversation_updated_at`, `conversation_summary`, `platform`, `is_starred`.

### `claude_code`

Reads two inputs, and parses both from the raw transcript record:

- `claude_code_transcripts*.parquet` from `LIFELAKE_CLAUDE_EXPORT_DIR`, with the columns `raw_json`, `transcript_rel_path` and `line_no`.
- Every `*.jsonl` file under `LIFELAKE_CLAUDE_PROJECTS`. Files modified within the last `LIFELAKE_RECENT_SECONDS` wait for a later refresh.

Rows are unique on `(session_id, uuid)`, and a parquet row wins over the same record from a jsonl file loaded in the same batch. Records without a `uuid` are bookkeeping and are not stored.

### `takeout`

Walks `LIFELAKE_TAKEOUT_DIR` and picks a parser from each file's path. Every row goes to `events`, and its `source` column names the kind.

| File | `events.source` |
|---|---|
| `MyActivity.json`, `MyActivity.html`, other `.json` or `.html` under `My Activity/` | `google.activity.<product>` |
| `watch-history.json`, `search-history.json` | `google.activity.<product>` |
| `*History.json` under `Chrome/` | `chrome_sync.visit` |
| `.json` with `timeline` or `location history` in its path | `google.location` |
| `.ics` | `calendar.event` |
| `.mbox` | `email` |

Other files are ignored, and so are paths that look like credential stores (`is_secret_path` in `lifelake/config.py`).

### `spotify`

Walks `LIFELAKE_SPOTIFY_DIR` and reads only these file names, ignoring case. Every other file is never opened, which includes `Userdata.json` and `Payments.json`.

| File | `events.source` |
|---|---|
| `Streaming_History_Audio_*.json`, `StreamingHistory*.json` | `spotify.play` |
| `SearchQueries.json` | `spotify.search` |
| `Playlist*.json` | `spotify.playlist` |
| `Inferences.json` | `spotify.inference` |
| `YourLibrary.json`, `Follow.json`, `Marquee.json` | `spotify.other.<name>` |

The extended history keeps the time, track or episode, artist or show, `ms_played`, platform and `skipped`. Its IP address, user agent and username fields are never copied into a row.

## The Source contract

A connector subclasses `Source` and sets a unique `name`. That name is the `source` value in the `manifest` and `runs` tables.

| Method | Job |
|---|---|
| `discover() -> list[Item]` | List the inputs. `Item.key` becomes `manifest.file`, so it must be stable between runs. Return an empty list when the input folder is missing. |
| `watermark(item) -> str \| None` | A cheap change marker. The default is `size:mtime_ns`. Return `None` to put a file off until a later refresh, for example while it is still being written. |
| `extract(items) -> Iterator[Extracted]` | Parse each `(item, watermark)` pair and yield one `Extracted`. All text must be redacted by this point. |
| `load(rows) -> int` | Insert rows and return how many were added. It runs inside the driver's transaction. |

`Extracted` holds the item, the file's content hash, the watermark, the rows by table name, redaction counts by type and the number of rows extracted.

`Source.refresh()` calls them in this order:

1. Call `discover()`. Drop any file named in `NEVER_INGEST`.
2. Compare each item's watermark with its manifest row. An equal watermark means the file is skipped without being opened.
3. Call `extract()` on the rest. If a file's content hash matches the manifest, only its watermark is updated and its rows are discarded.
4. Every 50,000 rows, and at the end, run `load()` and write the manifest rows in one transaction. A crash leaves no file half loaded.
5. Add a row to `runs` and the redaction counts to `redaction_log`.

## Rules for a connector

- **Redact everything.** `event_row()` in `lifelake/sources/events.py` does this for the `events` table. If you build rows for your own table, call `redact()` on each text value and pass the same `Counter` through.
- **Never store or log a matched value.** Only counts leave the redaction layer.
- **Read a fixed list of file types.** Do not walk a folder and parse whatever is there. Call `is_secret_path()` on the relative path.
- **Drop identity fields you do not need.** Build rows from named fields rather than copying a whole record.
- **Make inserts idempotent.** `db.insert_staged()` inserts only rows whose key is not in the table yet, so overlapping exports dedupe. `event_id` hashes the source, time and redacted content.
- **Report a bad file instead of crashing.** Set `self.failed` to a list and append the item key. The file gets no manifest row, so the next refresh tries it again, and its name goes into `runs.note`.

## Adding a connector

This example reads CSV files with the columns `time` and `text`. Save it as `lifelake/sources/notes.py`.

```python
"""One event per row of every *.csv file in a folder. Columns: time, text."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Iterator

from .. import db
from ..config import is_secret_path
from ..timeparse import parse_any
from .base import Extracted, Item, Source
from .events import event_row, load_events


class NotesSource(Source):
    name = "notes"

    def discover(self) -> list[Item]:
        root = Path(self.cfg.notes_dir)
        if not root.is_dir():
            return []
        rels = (p.relative_to(root).as_posix() for p in sorted(root.rglob("*.csv")))
        return [Item(key=rel, path=root / rel) for rel in rels if not is_secret_path(rel)]

    def extract(self, items) -> Iterator[Extracted]:
        for item, wm in items:
            counts: Counter = Counter()
            with open(item.path, newline="", encoding="utf-8") as f:
                rows = [event_row(counts, source="notes.entry", src_file=item.key, source_ref=i, actor="me",
                                  ts=parse_any(r["time"]), text=r["text"])
                        for i, r in enumerate(csv.DictReader(f))]
            yield Extracted(item, db.sha256_file(item.path), wm, {"events": rows}, counts, n=len(rows))

    def load(self, rows) -> int:
        return load_events(self.con, rows.get("events", []))
```

Then wire it in:

1. **Config.** In `lifelake/config.py`, add a field to `Config`:
   ```python
   notes_dir: Path = field(default_factory=lambda: _path("LIFELAKE_NOTES_DIR", "data/notes"))
   ```
2. **CLI.** In `lifelake/cli.py`, add `"notes"` to `SOURCES` and a branch to `source_class()`.
3. **Safe view.** In `lifelake/safe_views.sql`, add a view with no free text. Rows in `events` already appear in `safe.events`, so this step is optional for a simple connector:
   ```sql
   CREATE OR REPLACE VIEW safe.notes AS
   SELECT ts_utc, local_ts, tz, coalesce(length(text), 0) AS text_chars
   FROM lake.main.events WHERE source = 'notes.entry';
   ```
   The guard reads the view list from this file, so the view is queryable once it is there. Add a line for it to `TABLE_NOTES` in `lifelake/schema_doc.py`.
4. **New table, if you need one.** Add the DDL to `lifelake/db.py` and call `db.insert_staged()` from `load()`. To fill `local_ts` and `tz` from `tz_periods`, copy the `select=` argument that `load_events()` uses.
5. **Demo data and tests.** Add a writer under `lifelake/synth/` with invented values, including a planted fake secret. Add a test file under `tests/` that checks parsing, redaction and that a second refresh adds no rows. Build fake secrets by concatenation at runtime, so that `gitleaks` stays clean:
   ```python
   fake = "ghp_" + "Zq9" * 12
   ```
6. **Check.** Run `make demo`, `make test` and `make lint`. `make demo` ends with `lifelake scan`, which must report 0 hits.
