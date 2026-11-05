"""Planted-secret recall, false positives and idempotence of lifelake.redact, and the connectors' use of it."""
import json
import random
import string
from collections import Counter

from lifelake.redact import find_secrets, redact
from lifelake.sources.claude_ai import parse_rows
from lifelake.sources.claude_code import parse_record
from lifelake.sources.events import event_row

rng = random.Random(20240925)
B62 = string.ascii_letters + string.digits
PEM = "RSA PRIVATE " + "KEY-----"
TOKEN = "gh" + "p_" + "Zq9" * 12


def rnd(n, alphabet=B62):
    return "".join(rng.choice(alphabet) for _ in range(n))


def planted() -> list[tuple[str, str, str]]:
    """(type, secret, context text containing it). Six of each of 15 kinds -> 90 secrets."""
    makers = {
        "anthropic_key": lambda: "sk-" + "ant-api03-" + rnd(40, B62 + "-_") + "AA",
        "openai_key": lambda: rng.choice(["sk-" + "proj-", "sk-"]) + rnd(48),
        "github_token": lambda: rng.choice(["gh" + "p_", "gh" + "o_", "gh" + "s_"]) + rnd(36) if rng.random() < .6
        else "github" + "_pat_" + rnd(22) + "_" + rnd(59),
        "aws_access_key": lambda: "AK" + "IA" + rnd(16, string.ascii_uppercase + string.digits),
        "aws_secret_key": lambda: rnd(40, B62 + "/+"),
        "private_key": lambda: "-----BEGIN " + PEM + "\n" + "\n".join(rnd(64, B62 + "+/") for _ in range(6))
        + "\n-----END " + PEM,
        "url_credentials": lambda: rnd(12, string.ascii_letters + string.digits + "!*~"),
        "telegram_bot_token": lambda: str(rng.randint(10**8, 10**10 - 1)) + ":AA" + rnd(33, B62 + "_-"),
        "broker_account": lambda: rng.choice(["U", "DU"]) + str(rng.randint(10**6, 10**7 - 1)),
        "password": lambda: rnd(10, string.ascii_letters) + str(rng.randint(10, 99)) + rng.choice("!#%&?"),
        "stripe_key": lambda: rng.choice(["sk" + "_live_", "rk" + "_live_", "sk" + "_test_"]) + rnd(24),
        "google_api_key": lambda: "AI" + "za" + rnd(35, B62 + "_-"),
        "slack_token": lambda: "xo" + "xb-" + str(rng.randint(10**11, 10**12)) + "-" + rnd(24),
        "jwt": lambda: "ey" + "JhbGciOiJIUzI1NiJ9.ey" + "J" + rnd(40, B62 + "_-") + "." + rnd(43, B62 + "_-"),
        "high_entropy": lambda: rnd(40),
    }
    contexts = {
        "anthropic_key": ["export ANTHROPIC_API_KEY={s}", "client = Anthropic(api_key=\"{s}\")", "the key is {s}."],
        "openai_key": ["OPENAI_API_KEY={s}", "openai.api_key = '{s}'", "use {s} for now"],
        "github_token": ["git remote set-url origin https://x:{s}@github.com/a/b", "GH_TOKEN={s} gh pr list",
                         "token: {s}"],
        "aws_access_key": ["aws_access_key_id = {s}", "AWS_ACCESS_KEY_ID={s}", "id {s} in ~/.aws/credentials"],
        "aws_secret_key": ["aws_secret_access_key = {s}", "AWS_SECRET_ACCESS_KEY={s}", "\"aws_secret_key\": \"{s}\""],
        "private_key": ["cat ~/.ssh/id_rsa\n{s}\n", "key:\n{s}", "{s}"],
        "url_credentials": ["postgres://sam:{s}@db.example.com:5432/app", "https://admin:{s}@host.example.org/x",
                            "redis://default:{s}@10.0.0.2:6379"],
        "telegram_bot_token": ["TELEGRAM_BOT_TOKEN={s}", "https://api.telegram.org/bot{s}/sendMessage",
                               "bot = Bot(\"{s}\")"],
        "broker_account": ["Broker account {s} (live)", "accountId={s}", "Account `{s}` margin"],
        "password": ["password={s}", "PASSWORD: \"{s}\"", "mysql -u root --password={s} db", "\"pwd\": \"{s}\""],
        "stripe_key": ["STRIPE_SECRET_KEY={s}", "stripe.api_key = \"{s}\"", "key {s}"],
        "google_api_key": ["GOOGLE_API_KEY={s}", "maps?key={s}&q=x", "\"apiKey\": \"{s}\""],
        "slack_token": ["SLACK_BOT_TOKEN={s}", "Authorization: Bearer {s}", "token {s} works"],
        "jwt": ["Authorization: Bearer {s}", "cookie session={s}; Path=/", "jwt: {s}"],
        "high_entropy": ["export ACME_SERVICE_KEY={s}", "curl -H 'X-Api-Token: {s}' https://api.example.org",
                         "config.json: {{\"license\": \"{s}\"}}"],
    }
    out = []
    for typ, mk in makers.items():
        for i in range(6):
            s = mk()
            ctx = contexts[typ][i % len(contexts[typ])]
            out.append((typ, s, f"Some notes before. {ctx.format(s=s)} And after, see ticket 42.\n"))
    return out


def test_planted_secret_recall():
    cases = planted()
    types = {t for t, _, _ in cases}
    assert len(types) == 15 and len(cases) == 90
    caught, missed = 0, Counter()
    for typ, secret, text in cases:
        out = redact(text)
        # caught = no 8+ char piece of the secret survives
        pieces = [secret[i:i + 8] for i in range(0, max(1, len(secret) - 7), 4)]
        if all(p not in out for p in pieces if p.strip()):
            caught += 1
        else:
            missed[typ] += 1
    recall = caught / len(cases)
    print(f"\nplanted-secret recall: {caught}/{len(cases)} = {recall:.1%} over {len(types)} types; "
          f"missed by type: {dict(missed) or 'none'}")
    assert recall >= 0.95


def test_whole_corpus_redacted_is_clean():
    blob = "\n".join(t for _, _, t in planted())
    out = redact(blob)
    assert find_secrets(out) == Counter()
    assert find_secrets(out, entropy=True) == Counter()
    assert redact(out) == out  # idempotent


def test_counts_not_values():
    c = Counter()
    s = "sk-" + "ant-api03-" + "Ab3" * 12
    out = redact(f"key {s}", c)
    assert s not in out and "[REDACTED:anthropic_key]" in out
    assert dict(c) == {"anthropic_key": 1}
    assert all(isinstance(k, str) and "sk-ant" not in k for k in c)


BENIGN = [
    "cases = {\"password\": lambda: make_one()}",
    "session 7c1e92d4-3f0a-4b6d-8e21-5a9fd0c47b13 at commit 4e8a1f6c9b2d7035e1a4c8f2b6d90e3a7c5f1b28",
    "tool id toolu_01HXQ4NwT7bzkp9R3DD2gs6j and msg_014DfpWQsYcXTgHQMKDSncZl req_012BZ7y9Sp4gGKld8wmFj2Xb",
    "/home/sam/.claude/projects/-home-sam-desktop-projects-2027-Hobby-Apps/a1c47e09.jsonl",
    "model claude-opus-4-5-20251101 and claude_code_transcripts_2024-09-13.parquet",
    "def login(password=password, api_key=os.environ['KEY']): token = self.token",
    "password: str = Field(...); secret = settings.SECRET_KEY; api_key=None",
    "sha512-R1eTpHqYLMVdK1AL7s3oFuTVDnH5IXl9s5NVv2sSaR0fGWiAcG6Dn0Hj8t5aw8Y7arYIoiCaLk2Vg3aW/0k1eR==",
    "Sam_Sample_Resume_2026_Data_Analyst_Final_v3.pdf and Acme_Graduate_Analyst_2027_Application",
    "SetupExecutionTimeoutMilliseconds and ThisIsAVeryLongCamelCaseIdentifierName2026",
    "the U.S. market opened; see U2 and USB3 ports, Unit 1234567",
    "git@github.com:example-org/Sample_Job_Tracker.git and https://github.com/example-org/repo",
    "Price 1234567890:00 and ratio 12345678:1 are not tokens",
]


def test_benign_text_unchanged():
    changed = [b for b in BENIGN if redact(b) != b]
    assert changed == []


def test_percent_encoded_secrets_are_caught():
    """A search or maps URL encodes what was typed, which would otherwise hide all of these."""
    pw = "Zq9" * 4
    account = "DU" + "3981237"
    cases = {
        "broker_account": f"https://example.org/s?q=accountId%3D{account}",
        "github_token": "https://example.org/s?q=token%3D" + TOKEN,
        "password": f"https://example.org/s?q=password%3D{pw}",
        "url_credentials": f"https://example.org/s?u=postgres%3A%2F%2Fapp%3A{pw}%40db.example.com",
    }
    for kind, url in cases.items():
        c = Counter()
        out = redact(url, c, entropy=False)
        assert c == Counter({kind: 1}), url
        assert f"[REDACTED:{kind}]" in out and "Zq9" not in out and "3981237" not in out
        assert out.startswith("https://example.org/s?")
        assert find_secrets(url) == Counter({kind: 1})  # the scan sees them too
        assert find_secrets(out) == Counter() and redact(out) == out


def test_percent_escapes_without_a_secret_stay_as_they_were():
    for text in ("https://example.org/s?q=a%20b%26c+d", "100% sure", "50%3D", "%", "see %20 and %zz"):
        assert redact(text) == text


# --- what the connectors must do with it ------------------------------------------------------

def event(**kw):
    return event_row(Counter(), source="x", src_file="f", **kw)


def test_event_row_redacts_reference_and_device():
    """A playlist's name becomes its source_ref, and a search's platform becomes its device."""
    c = Counter()
    row = event_row(c, source="x", src_file="f", source_ref=f"mix {TOKEN}", device=f"dev {TOKEN}")
    assert TOKEN not in row["source_ref"] and TOKEN not in row["device"]
    assert c["github_token"] == 2


def test_event_row_keeps_plain_reference():
    row = event(source_ref=12, device="phone")
    assert row["source_ref"] == "12" and row["device"] == "phone"
    assert event()["source_ref"] is None


def test_payload_secret_shown_only_by_its_key_is_redacted():
    """No leaf looks like a secret here; only the key says what the value is."""
    value = "Zq9" * 4
    row = event(payload={"password": value, "note": "fine"})
    assert value not in row["payload"]
    assert json.loads(row["payload"]) == {"password": "[REDACTED:password]", "note": "fine"}
    assert find_secrets(row["payload"]) == Counter()


def test_payload_redaction_is_not_counted_twice():
    c = Counter()
    event_row(c, source="x", src_file="f", payload={"note": f"token {TOKEN}"})
    assert c == Counter({"github_token": 1})


def test_url_with_credentials_gets_the_right_domain():
    row = event(url="https://admin:" + "Zq9" * 4 + "@intranet.example.org/x")
    assert "Zq9" not in row["url"] and row["domain"] == "intranet.example.org"


def test_entropy_secret_in_url_and_reference_is_redacted():
    """A token typed into a search lands in the URL, and a playlist's name in source_ref; regexes alone miss both."""
    opaque = "bcdfghjklmnpqrstvwxz" + "BCDFGHJKLMNPQRST" + "0123"
    row = event(url=f"https://www.example.org/search/license+{opaque}+soon", source_ref=f"mix {opaque}")
    assert opaque not in row["url"] and opaque not in row["source_ref"]
    assert row["domain"] == "example.org"


def test_short_url_tokens_survive():
    url = "https://www.youtube.com/watch?v=abcDEF12345&list=recent-garden-videos-2024"
    assert event(url=url)["url"] == url


def test_claude_code_redacts_before_capping():
    """A cap landing inside a token must not leave a part of it that no pattern can recognize."""
    rec = {"type": "assistant", "uuid": "u", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "x" * 7960 + " " + TOKEN}}]}}
    row = parse_record(rec, "p/s.jsonl", 1, "live", "f", Counter(), (8000, 20000))
    assert "Zq9" not in row["tool_input"]
    res = {"type": "user", "uuid": "r", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "y" * 7990 + " " + TOKEN}]}}
    row = parse_record(res, "p/s.jsonl", 1, "live", "f", Counter(), (8000, 20000))
    assert "Zq9" not in row["tool_result"]


def test_claude_code_redacts_branch_name():
    rec = {"type": "user", "uuid": "u", "gitBranch": f"fix/{TOKEN}", "message": {"role": "user", "content": "hi"}}
    assert TOKEN not in parse_record(rec, "p/s.jsonl", 1, "live", "f", Counter())["git_branch"]


def test_claude_ai_redacts_before_capping():
    extra = {"attachments": [{"file_name": "n.txt", "extracted_content": "x" * 190 + " " + TOKEN}]}
    d = {"message_id": "m", "conversation_id": "c", "extra_json": json.dumps(extra)}
    msg, _ = parse_rows(d, "f", Counter(), 200)
    assert "Zq9" not in msg["attachments"]
