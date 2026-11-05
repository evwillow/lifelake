-- Safe views: the only thing the query layer (and any LLM on top of it) can read.
-- query.SafeLake attaches the lake read-only as catalog `lake`, creates schema
-- scratch.safe in a throwaway catalog and runs this file there.
--
-- Left out on purpose: every column that holds free text (message bodies, thinking,
-- tool I/O, attachments, conversation names, event text/context, URLs, raw payloads)
-- and raw coordinates. Only lengths, domains and typed fields are derived from them.
-- Spotify artist and track names are the exception: they are the point of the table,
-- and they went through redaction at ingest like everything else.

CREATE OR REPLACE VIEW safe.claude_ai_conversations AS
SELECT conversation_id, created_utc, updated_utc, local_created_ts, tz,
       platform, is_starred, src_file
FROM lake.main.claude_ai_conversations;

CREATE OR REPLACE VIEW safe.claude_ai_messages AS
SELECT message_id, conversation_id, parent_id, role, message_index, ts_utc,
       local_ts, tz, model, truncated,
       coalesce(length(text), 0)        AS text_chars,
       coalesce(length(attachments), 0) AS attachment_chars,
       src_file
FROM lake.main.claude_ai_messages;

CREATE OR REPLACE VIEW safe.claude_code_messages AS
SELECT session_id, uuid, parent_uuid, root_session_id, agent_id, workflow_id,
       transcript_kind, project_key, rel_path, line_no, type, subtype, role,
       ts_utc, local_ts, tz, git_branch, cli_version, model, entrypoint,
       user_type, is_sidechain, is_meta, prompt_source, origin_kind,
       prompt_id, request_id, is_real_prompt, tool_names,
       coalesce(length(text), 0) AS text_chars,
       input_tokens, output_tokens, cache_read_input_tokens,
       cache_creation_input_tokens, stop_reason, origin, src_file
FROM lake.main.claude_code_messages;

CREATE OR REPLACE VIEW safe.events AS
SELECT event_id, source, ts_utc, local_ts, tz, device, actor, domain,
       coalesce(length(text), 0) AS text_chars, src_file
FROM lake.main.events;

CREATE OR REPLACE VIEW safe.spotify_plays AS
SELECT ts_utc, local_ts, tz, device,
       json_extract_string(payload, '$.artist')             AS artist,
       text                                                 AS track,
       cast(json_extract(payload, '$.ms_played') AS BIGINT) AS ms_played,
       json_extract_string(payload, '$.platform')           AS platform,
       cast(json_extract(payload, '$.skipped') AS BOOLEAN)  AS skipped
FROM lake.main.events WHERE source = 'spotify.play';

CREATE OR REPLACE VIEW safe.google_activity AS
SELECT ts_utc, local_ts, tz, json_extract_string(payload, '$.product') AS product, domain
FROM lake.main.events WHERE source LIKE 'google.activity.%';

CREATE OR REPLACE VIEW safe.web_visits AS
SELECT ts_utc, local_ts, tz, device, domain
FROM lake.main.events WHERE source = 'chrome_sync.visit';

CREATE OR REPLACE VIEW safe.location_points AS
SELECT ts_utc, local_ts, tz FROM lake.main.events WHERE source = 'google.location';

CREATE OR REPLACE VIEW safe.calendar_events AS
SELECT ts_utc, local_ts, tz, coalesce(length(text), 0) AS title_chars
FROM lake.main.events WHERE source = 'calendar.event';

CREATE OR REPLACE VIEW safe.email_meta AS
SELECT ts_utc, local_ts, tz, actor, json_extract_string(payload, '$.sender_domain') AS sender_domain,
       coalesce(length(text), 0) AS body_chars
FROM lake.main.events WHERE source = 'email';

CREATE OR REPLACE VIEW safe.manifest AS
SELECT source, file, sha256, rows, watermark, ts FROM lake.main.manifest;

CREATE OR REPLACE VIEW safe.redaction_log AS
SELECT run_id, source, type, hits FROM lake.main.redaction_log;

CREATE OR REPLACE VIEW safe.runs AS
SELECT run_id, source, started, seconds, files_seen, files_loaded,
       files_skipped, rows_extracted, rows_added, status, note
FROM lake.main.runs;

CREATE OR REPLACE VIEW safe.tz_periods AS
SELECT tz, start_utc, end_utc, note FROM lake.main.tz_periods;
