"""Every parameter of the synthetic persona, in one visible block.

All values below are hand-set. Nothing here was fitted to, or read from, anyone's real data.
Each entry's comment names the distribution it drives. Counts are per year at scale 1.0.
"""
from __future__ import annotations

PARAMS = {
    # ------------------------------------------------------------------ calendar (invented)
    "first_day": "2024-01-02",          # first day with activity; keeps every record inside 2024 UTC
    "last_day": "2024-12-30",           # last day with activity; same reason
    "home_tz": "America/Chicago",
    "trip_tz": "Europe/Lisbon",
    "trip_first": "2024-06-03",
    "trip_last": "2024-06-16",
    "snapshot": "2024-12-31T00:00:00+00:00",  # mtime of files whose records carry no time
    # Bernoulli: does this day have any activity in a given stream?
    "active_weekday": 0.88,
    "active_weekend": 0.65,
    # Fixed multiplier on the Poisson daily rate during the trip.
    "trip_rate": 0.5,
    # Two-component normal mixture for local hour of day: (weight, mean, sd), wrapped into 0..24.
    "hour_mixture": ((0.55, 10.5, 2.0), (0.45, 20.5, 2.3)),

    # ------------------------------------------------------------------ claude.ai
    "ai_conversations": 150,            # Poisson per active day; the daily rate is set to hit this total
    "ai_turns": (1.3, 0.6),             # lognormal(mu, sigma): human turns per conversation
    "ai_gap_seconds": (3.5, 0.9),       # lognormal: seconds between messages
    "ai_human_chars": (4.7, 0.7),       # lognormal: human message length
    "ai_assistant_chars": (6.2, 0.7),   # lognormal: assistant message length
    "ai_models": (("claude-sonnet-demo", 0.6), ("claude-opus-demo", 0.3), ("claude-haiku-demo", 0.1)),  # categorical
    "ai_platforms": (("web", 0.7), ("ios", 0.2), ("android", 0.1)),  # categorical
    "ai_starred": 0.08,                 # Bernoulli: conversation is starred
    "ai_truncated": 0.02,               # Bernoulli: assistant message was truncated
    "ai_attachment": 0.08,              # Bernoulli: human message has an attachment with extracted text
    "ai_file": 0.05,                    # Bernoulli: human message has an uploaded file
    "ai_duplicate_row": 0.01,           # Bernoulli: a row is repeated in the export
    "ai_code_copies": 40,               # fixed: claude_code rows the export also carries; the connector drops them

    # ------------------------------------------------------------------ Claude Code
    "cc_sessions": 400,                 # Poisson per active day; the daily rate is set to hit this total
    "cc_project_zipf": 1.1,             # Zipf exponent over projects
    "cc_branches": (("main", 0.6), ("feature/search", 0.15), ("fix/dates", 0.15), ("chore/deps", 0.1)),  # categorical
    "cc_prompts": (1.1, 0.7),           # lognormal: typed prompts per session
    "cc_tool_loops": 1.6,               # Poisson: tool call and result pairs after a prompt
    "cc_gap_seconds": (2.8, 0.9),       # lognormal: seconds between records
    "cc_prompt_chars": (4.6, 0.7),      # lognormal: typed prompt length
    "cc_reply_chars": (5.9, 0.8),       # lognormal: assistant reply length
    "cc_result_chars": (5.3, 1.0),      # lognormal: tool result length
    "cc_thinking_chars": (5.0, 0.7),    # lognormal: thinking block length
    "cc_input_tokens": (5.0, 1.2),      # lognormal: usage.input_tokens
    "cc_output_tokens": (5.5, 1.0),     # lognormal: usage.output_tokens
    "cc_cache_read_tokens": (10.0, 1.0),      # lognormal: usage.cache_read_input_tokens
    "cc_cache_creation_tokens": (7.0, 1.2),   # lognormal: usage.cache_creation_input_tokens
    "cc_models": (("claude-sonnet-demo", 0.7), ("claude-opus-demo", 0.25), ("claude-haiku-demo", 0.05)),  # categorical
    "cc_tools": (("Bash", 0.34), ("Read", 0.28), ("Edit", 0.16), ("Grep", 0.08), ("Glob", 0.05),
                 ("Write", 0.05), ("WebFetch", 0.02), ("Task", 0.02)),  # categorical
    "cc_prompt_sources": (("typed", 0.86), ("queued", 0.1), ("suggestion_accepted", 0.04)),  # categorical
    "cc_thinking": 0.3,                 # Bernoulli: assistant record has a thinking block
    "cc_parallel_tools": 0.1,           # Bernoulli: assistant record calls two tools
    "cc_image_prompt": 0.01,            # Bernoulli: typed prompt also carries an image block
    "cc_harness_record": 0.2,           # Bernoulli: a non-prompt user record follows a prompt
    "cc_attachment_record": 0.05,       # Bernoulli: an attachment record follows a prompt
    "cc_compact_record": 0.02,          # Bernoulli: a compaction record follows a prompt
    "cc_legacy_format": 0.25,           # Bernoulli: session predates the promptSource field
    "cc_export_cutoff": "2024-10-01",   # fixed: the export snapshot; later sessions exist only live
    "cc_overlap": 0.25,                 # Bernoulli: a pre-cutoff session also has a live file
    "cc_overlap_keep": 0.7,             # fixed: share of an overlapping session's lines already in the export
    "cc_subagent": 0.08,                # Bernoulli: session spawned subagents
    "cc_subagent_files": 1.0,           # 1 + Poisson: subagent transcripts in such a session
    "cc_subagent_turns": 2.0,           # 1 + Poisson: prompt and reply pairs per subagent transcript
    "cc_duplicate_row": 0.002,          # Bernoulli: an export row is repeated
    "cc_torn_line": 0.02,               # Bernoulli: a live file ends in a half-written line

    # ------------------------------------------------------------------ Google Takeout
    "n_search": 2300,                   # Poisson per active day
    "n_youtube": 1500,                  # Poisson per active day
    "n_maps": 350,                      # Poisson per active day (My Activity HTML)
    "n_chrome": 4600,                   # Poisson per active day
    "n_location": 2000,                 # Poisson per active day
    "n_calendar": 220,                  # Poisson per active day
    "n_email": 380,                     # Poisson per active day
    "site_zipf": 1.05,                  # Zipf exponent over sites, for Chrome visits and searches
    "chrome_devices": (("cid-laptop-demo", 0.7), ("cid-phone-demo", 0.3)),  # categorical
    "search_visit": 0.2,                # Bernoulli: an activity row is a site visit, not a search
    "html_narrow_nbsp": 0.5,            # Bernoulli: HTML time uses U+202F before AM/PM, as newer exports do
    "html_location": 0.4,               # Bernoulli: HTML cell has a general-area location
    "location_sd_degrees": 0.02,        # normal: scatter of a location point around the current city
    "location_legacy_ms": 0.2,          # Bernoulli: location record uses timestampMs
    "location_accuracy": (3.5, 0.8),    # lognormal: accuracy in metres
    "location_sources": (("WIFI", 0.6), ("GPS", 0.3), ("CELL", 0.1)),  # categorical
    "calendar_all_day": 0.15,           # Bernoulli: all-day event
    "calendar_utc": 0.25,               # Bernoulli: DTSTART in UTC rather than with a TZID
    "email_sent": 0.3,                  # Bernoulli: mail was sent by the persona
    "email_chars": (5.3, 0.7),          # lognormal: mail body length
    "email_multipart": 0.25,            # Bernoulli: multipart/alternative
    "email_base64": 0.15,               # Bernoulli: plain part is base64 encoded (mails without a secret)

    # ------------------------------------------------------------------ Spotify
    "n_artists": 120,                   # fixed: size of the invented catalogue
    "artist_zipf": 1.1,                 # Zipf exponent for artist popularity
    "track_zipf": 0.8,                  # Zipf exponent for tracks within an artist
    "tracks_per_artist": 5.0,           # 1 + Poisson
    "listening_sessions": 900,          # Poisson per active day
    "plays_per_session": 9.0,           # 1 + Poisson
    "track_ms": (12.2, 0.25),           # lognormal: full track length in milliseconds
    "skip": 0.18,                       # Bernoulli: the play was skipped
    "skip_fraction": (0.02, 0.3),       # uniform(a, b): share of a skipped track that was played
    "podcast": 0.06,                    # Bernoulli: the play is a podcast episode
    "nameless_play": 0.003,             # Bernoulli: the play has no track metadata and 0 ms
    "spotify_platforms": (("android", 0.5), ("web", 0.3), ("ios", 0.2)),  # categorical
    "extended_until": "2024-09-01",     # fixed: earlier plays are in the extended file, later in the account file
    "n_spotify_searches": 300,          # Poisson per active day
    "n_playlists": 8,                   # fixed
    "playlist_items": 12.0,             # 1 + Poisson
    "n_inferences": 18,                 # fixed
    "n_library": 200,                   # fixed

    # ------------------------------------------------------------------ fake secrets
    # Bernoulli per text field. The first field of each kind always gets one, so every field kind
    # shows redaction at any scale. Secrets are built at runtime from the seeded RNG.
    "secret_rates": {
        "claude_ai.text": 0.02, "claude_ai.name": 0.01, "claude_ai.summary": 0.01, "claude_ai.attachment": 0.1,
        "claude_ai.file_name": 0.02,
        "claude_code.prompt": 0.015, "claude_code.reply": 0.002, "claude_code.thinking": 0.002,
        "claude_code.tool_input": 0.004, "claude_code.tool_result": 0.006, "claude_code.subagent": 0.01,
        "takeout.search": 0.003, "takeout.youtube": 0.002, "takeout.maps": 0.005, "takeout.chrome_title": 0.002,
        "takeout.chrome_url": 0.002, "takeout.location": 0.001, "takeout.calendar": 0.02, "takeout.email_subject": 0.01,
        "takeout.email_body": 0.05,
        "spotify.search": 0.02, "spotify.playlist": 0.1, "spotify.library": 0.01, "spotify.inference": 0.05,
    },
}
