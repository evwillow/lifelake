"""Word lists and templates for the invented persona, Sam Sample, and their hobby software projects."""
from __future__ import annotations

import random

PERSON_NAME = "Sam Sample"
PERSON_EMAIL = "sam@example.com"
HOME_DIR = "/home/sam/projects"

PROJECTS = ("demo-api", "garden-planner", "recipe-box", "trail-log", "photo-sorter", "bike-fit", "tide-table")

TOPICS = (
    "pagination", "input validation", "the database schema", "seed data", "the retry helper", "date parsing",
    "the watering schedule", "error messages", "the export button", "unit tests", "the config loader",
    "the search index", "rate limiting", "the login form", "the plant catalogue", "CSV import", "caching",
    "the settings page", "logging", "the sync job",
)
FILES = ("app.py", "models.py", "routes.py", "schema.sql", "utils.py", "config.toml", "tests/test_app.py",
         "README.md", "static/main.js", "jobs/sync.py", "templates/index.html")
FUNCTIONS = ("load_config", "parse_date", "next_watering", "list_items", "save_item", "retry", "render_page",
             "import_csv", "paginate", "validate_input")
COMMANDS = ("pytest -q", "ruff check .", "git status", "git diff --stat", "ls -la", "python -m app --dry-run",
            "make test", "git log --oneline -5", "grep -n TODO app.py", "sqlite3 app.db .schema")

PROMPTS = (
    "Can you add {topic} to {project}?",
    "The tests for {topic} fail after my last change, please take a look.",
    "Please refactor {fn} in {file} so it is easier to read.",
    "Why does {fn} return nothing when the input is empty?",
    "Write a small test for {fn}.",
    "Let's clean up {topic} before the next release.",
    "How should I structure {topic} in {file}?",
    "Now update the docs for {topic}.",
    "Good. Next, look at {file} and tell me what looks wrong.",
)
REPLIES = (
    "I looked at {file} and the issue comes from {fn}.",
    "Here is a plan for {topic}: first add a failing test, then change {fn}, then update the docs.",
    "The change is small and only touches {file}.",
    "I updated {fn} so it handles an empty input and added a test for that case.",
    "All tests pass now. The remaining work on {topic} can wait for a later change.",
    "One option is to keep {topic} in {file}; another is to move it into its own module.",
    "This should not affect {project} users, because {fn} is only called from the sync job.",
    "I would keep this simple for a hobby project and avoid a new dependency.",
)
THOUGHTS = (
    "The user wants {topic}. I should read {file} first.",
    "{fn} is probably the right place for this.",
    "A small change is enough here, and a test will keep it honest.",
    "Check whether {file} already handles this case.",
)
RESULTS = (
    "ok: 12 passed in 0.31s",
    "{file}:42: unused variable in {fn}",
    "def {fn}(value):\n    return value",
    "On branch main, nothing to commit",
    "{file} | 4 ++--",
    "# {project}\nA small hobby project.",
    "no matches found",
    "updated {file}",
)
TITLES = (
    "Help with {topic}", "{topic} in {project}", "Planning {project}", "Debugging {fn}", "Ideas for {project}",
    "Notes on {topic}", "Reviewing {file}",
)
SUMMARIES = (
    "Sam asks about {topic} in {project} and gets a short plan.",
    "A discussion of {fn} and how to test it.",
    "Working through {topic} for a hobby project.",
)
ATTACHMENT_NAMES = ("notes.txt", "todo.md", "error_log.txt", "plan.md", "draft_schema.sql")
UPLOAD_NAMES = ("screenshot.png", "sketch.jpg", "layout.pdf", "plot.png")

SEARCH_TERMS = (
    "python sqlite upsert", "when to plant tomatoes", "how to prune roses", "css grid example", "pytest fixtures",
    "best compost ratio", "git rebase onto", "bike chain lube", "sourdough starter ratio", "weather this weekend",
    "duckdb window functions", "raised bed soil depth", "trail map near me", "regex lookahead example",
    "how long to boil eggs", "home network setup", "tide table explained", "json schema validation",
)
VIDEO_TOPICS = (
    "Pruning tomato plants", "Beginner sourdough", "SQL window functions explained", "Fixing a flat tire",
    "Building a raised bed", "Python packaging basics", "Night sky timelapse", "Trail running form",
    "Compost in 5 minutes", "Learning git the slow way",
)
CHANNELS = ("Garden Channel Demo", "Code Channel Demo", "Bike Channel Demo", "Kitchen Channel Demo")
PLACES = ("coffee shop", "hardware store", "garden center", "bike repair", "bakery", "public library", "trailhead")
SITE_WORDS = ("guide", "docs", "notes", "forum", "article", "page", "topic", "howto", "review", "list")
# First is most popular under the Zipf draw. "www." is dropped from the domain at ingest.
SITES = ("www.example.com", "en.wikipedia.org", "www.example.org", "docs.example.com", "news.example.net",
         "forum.example.org", "blog.example.com", "shop.example.net", "weather.example.org", "recipes.example.com",
         "www.wikipedia.org", "maps.example.net")
CITIES = {"home": (41.88, -87.63), "trip": (38.72, -9.14)}  # invented persona's two cities

CALENDAR_TITLES = ("Garden club meeting", "Dentist", "Code review with Alex", "Plant seedlings", "Bike service",
                   "Dinner with friends", "Community workday", "Weekly planning", "Book club", "Team call")
CONTACTS = ("Alex Example <alex@example.org>", "Jordan Sample <jordan@example.com>", "Taylor Demo <taylor@example.net>",
            "Garden Club <news@club.example.org>", "Demo Store <orders@shop.example.com>")
SUBJECTS = ("Plans for {topic}", "Question about {project}", "Your order has shipped", "Garden club notes",
            "Re: {topic}", "Weekend plans", "Receipt for your purchase", "Notes from the meeting")
MAIL_BODIES = (
    "Hi, here are my notes on {topic}.",
    "Could you take a look at {project} when you have a moment?",
    "Thanks for the help last week.",
    "The next garden club meeting moves to Saturday morning.",
    "Your order is on its way and should arrive soon.",
    "I pushed a fix to {file}, so the tests should pass now.",
    "Let me know if that time still works for you.",
)
MAIL_LABELS_IN = ("Inbox", "Inbox,Important", "Inbox,Category Updates", "Archived", "Category Promotions")
MAIL_LABELS_OUT = ("Sent", "Sent,Important")

ARTIST_ADJECTIVES = (
    "Amber", "Silver", "Quiet", "Paper", "Golden", "Hollow", "Electric", "Northern", "Crimson", "Distant", "Neon",
    "Velvet", "Wandering", "Copper", "Midnight", "Glass", "Summer", "Winter", "Lunar", "Rusty",
)
ARTIST_NOUNS = (
    "Harbor", "Lanterns", "Foxes", "Rivers", "Meadow", "Engines", "Comets", "Sparrows", "Orchard", "Tides", "Mirrors",
    "Pines", "Static", "Canyons", "Birches", "Ferns", "Kites", "Islands", "Signals", "Clouds",
)
TRACK_WORDS = ("Morning", "Letters", "Slow", "Home", "Echo", "Rain", "Light", "Roads", "Drift", "Ocean", "Fire",
               "Shadow", "Garden", "Stars", "Waves", "Sunday", "Radio", "Dust", "Bloom", "Vapor")
PODCASTS = ("Compost Corner Demo", "The Hobby Coder Demo", "Bike Shed Radio Demo")
SPOTIFY_QUERIES = ("focus music", "acoustic morning", "gardening podcast", "running mix", "rain sounds", "lofi coding")
INFERENCES = (
    "Demo segment: hobby gardeners", "Demo segment: home cooks", "Demo segment: casual cyclists",
    "Demo segment: podcast listeners", "Demo segment: coffee drinkers", "Demo segment: weekend hikers",
    "Demo segment: indie music", "Demo segment: acoustic music", "Demo segment: electronic music",
    "Demo segment: early risers", "Demo segment: late listeners", "Demo segment: commuters",
    "Demo segment: students of languages", "Demo segment: board games", "Demo segment: home improvement",
    "Demo segment: travel planners", "Demo segment: camera owners", "Demo segment: tea drinkers",
)


def fill(rng: random.Random, template: str, project: str | None = None) -> str:
    return template.format(topic=rng.choice(TOPICS), project=project or rng.choice(PROJECTS),
                           file=rng.choice(FILES), fn=rng.choice(FUNCTIONS))


def compose(rng: random.Random, pool, target_chars: float, project: str | None = None, cap: int = 4000) -> str:
    """Sentences from a template pool until the text reaches a drawn length (always at least one)."""
    out, n = [], 0
    limit = min(target_chars, cap)
    while True:
        s = fill(rng, rng.choice(pool), project)
        out.append(s)
        n += len(s) + 1
        if n >= limit:
            return " ".join(out)
