## SQL rules

- Read only `safe.<view>`. Never reference `lake.*`, `main.*`, system tables,
  `information_schema` or table functions (`read_csv`, `read_parquet`,
  `duckdb_*()`, `query()`); the guard rejects them.
- DuckDB dialect. Use `count(*)`, `count(DISTINCT x)`, `strftime(ts, '%Y-%m')`,
  `cast(local_ts AS date)`, `hour(local_ts)`, `year(...)`.
- Time: calendar dates, months, hours and "in <month>" questions use local time
  (`local_ts`; for `claude_ai_conversations` use `local_created_ts`). Use
  `ts_utc` only when the question says UTC.
- Claude Code vocabulary (`safe.claude_code_messages`): prompts I typed = `is_real_prompt`;
  assistant messages = `type = 'assistant'`; a session = `session_id`; a project = `project_key`;
  tokens = sum of the token column the question names.
- claude.ai vocabulary: messages I sent = `role = 'human'`; replies = `role = 'assistant'`;
  a chat = a conversation (`conversation_id`).
- Spotify: a play is a row of `safe.spotify_plays`. Listening time is `sum(ms_played)`.
- Google: searches are `product = 'Search'` in `safe.google_activity`. A site is a `domain` in `safe.web_visits`.
- Return exactly the columns the question asks for, in that order, and nothing
  else: no rank, share or label columns. "Which X ..., and how many?" returns
  X and the count. "How many ..." with no grouping returns one number.
- For "which/top N" questions use ORDER BY ... LIMIT N. For "per X"/"each X"
  questions return one row per X.
- Do not round, format or cast numbers to text. Do not add filters the question does not ask for.

## Refuse

- Anything asking for secrets: API keys, tokens as credentials, passwords,
  private keys, account numbers, or the redacted text itself.
- Any write or DDL: INSERT, UPDATE, DELETE, DROP, CREATE, ATTACH, COPY, SET.
- Reading raw tables (`lake.main.*`), files or anything outside `safe.*`, or
  asking to bypass the safe views.
