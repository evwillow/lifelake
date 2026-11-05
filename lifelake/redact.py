"""Secret redaction applied to every text value before it enters the lake.

Pass 1 is a set of typed regexes. Pass 2 runs a Shannon-entropy test, but only on
candidate tokens (long, mixed-class runs) that are left after pass 1. Every hit is
replaced with ``[REDACTED:<type>]``. Callers get counts by type and never the values.

Speed: Python's ``re`` is fast only when a pattern starts with a literal, so each
pattern starts with one where possible, carries a cheap prefilter (``hint``), and
checks its left word boundary in code (``lb``) instead of with a leading ``\\b``.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote_plus

MARK = "[REDACTED:{}]"
_NR = r"(?!\[redacted)(?!\[REDACTED)"  # value must not already be a redaction marker
_KEYWORDS = ("password", "passwd", "passphrase", "pwd", "secret", "api_key", "apikey", "api-key",
             "access_token", "accesstoken", "auth_token", "authtoken", "bot_token", "bottoken",
             "private_key", "privatekey")


@dataclass(frozen=True)
class Pattern:
    type: str
    rx: re.Pattern
    group: str | None = None  # replace only this named group (else the whole match)
    hint: tuple[str, ...] | re.Pattern | None = None  # skip text lacking any of these
    lb: bool = False  # require a non-word char (or start) before the match
    lower: bool = False  # match against text.lower() (pattern written in lower case)


def _p(t, rx, **kw):
    return Pattern(t, re.compile(rx), **kw)


PATTERNS: list[Pattern] = [
    _p("private_key",
       r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
       r"(?:[\s\S]{0,20000}?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|[A-Za-z0-9+/=\s\\]*)",
       hint=("-----BEGIN",)),
    _p("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{20,}", hint=("sk-ant-",)),
    _p("openai_key", r"sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}", hint=("sk-",), lb=True),
    _p("github_token", r"(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})",
       hint=("gh", "github_pat_"), lb=True),
    _p("aws_access_key", r"(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}(?![0-9A-Za-z])",
       hint=("AKIA", "ASIA", "ABIA", "ACCA"), lb=True),
    _p("aws_secret_key",
       r"(?P<k>aws_?secret_?(?:access_?)?key[\"']?\s*[:=]\s*[\"']?)" + _NR + r"(?P<v>[a-z0-9/+=]{40})",
       group="v", hint=("aws",), lower=True),
    _p("stripe_key", r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}", hint=("_live_", "_test_"), lb=True),
    _p("google_api_key", r"AIza[0-9A-Za-z_\-]{35}", hint=("AIza",), lb=True),
    _p("slack_token", r"xox[abprs]-[A-Za-z0-9\-]{10,}", hint=("xox",), lb=True),
    _p("telegram_bot_token", r"(?:(?<=bot)|(?<![A-Za-z0-9_]))[0-9]{8,10}:[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])",
       hint=re.compile(r":[A-Za-z0-9_\-]{35}")),  # "bot<token>" as in api.telegram.org/bot<token>/
    _p("jwt", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", hint=("eyJ",)),
    _p("url_credentials",
       r"(?P<k>://[^\s:/@\"'<>]{1,100}:)" + _NR + r"(?P<v>[^\s@/\"'<>$\{]{1,200})(?=@)",
       group="v", hint=("://",)),
    _p("broker_account", r"(?:DU|U)[1-9][0-9]{6,7}(?![0-9A-Za-z])", hint=("U",), lb=True),
    _p("password",
       r"(?P<k>(?:" + "|".join(re.escape(k) for k in _KEYWORDS) + r")[\"']?\s*[:=]\s*[\"']?)" + _NR +
       r"(?P<v>[^\s\"'`,;(){}\[\]<>]{6,200})",
       group="v", hint=_KEYWORDS, lb=True, lower=True),
]

# Values after "password=" that are code or placeholders, not secrets.
_CODE_VALUE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z_.]*:?|\$[A-Za-z_{(].*|%.*|\*+|x+|X+|\.+|-+|null|none|true|false|undefined|"
    r"(?:os|process|self|settings|config|args|env|cfg|opts|options|params|req|request)\b.*)$",
    re.I)

# Runs of URL characters holding a percent escape. A search URL encodes what was typed, and encoding hides a secret
# from the patterns: "%3Dghp_..." has a word character on its left and "password%3D..." has no "=".
_PCT_RUN = re.compile(r"[^\s\"'<>`]*%[0-9A-Fa-f]{2}[^\s\"'<>`]*")

# --- entropy pass -----------------------------------------------------------------
_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_\-+])[A-Za-z0-9_\-+]{32,}={0,2}(?![A-Za-z0-9_\-+])")
_SEP = re.compile(r"[_\-+]")
_HEXISH = re.compile(r"^[0-9a-fA-F\-]+$")
_ID_PREFIX = ("toolu_", "srvtoolu_", "msg_", "req_", "file_", "call_", "chatcmpl-", "sha256-",
              "sha512-", "sha1-", "wf_", "run_", "compl_", "batch_", "org-", "user_", "sess_",
              "session_")
_VOWELS = set("aeiouAEIOU")
ENTROPY_MIN = 4.0          # core segment has upper, lower and digit
ENTROPY_MIN_TWO_CLASS = 4.2
CORE_MIN = 20              # longest run between separators


def shannon(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def is_entropy_secret(tok: str) -> bool:
    body = tok.rstrip("=")
    if _HEXISH.match(body) or body.startswith(_ID_PREFIX):
        return False
    core = max(_SEP.split(body), key=len)
    if len(core) < CORE_MIN:  # snake/kebab identifiers, file names, project keys
        return False
    lower = any(c.islower() for c in core)
    upper = any(c.isupper() for c in core)
    digit = any(c.isdigit() for c in core)
    classes = lower + upper + digit
    if classes < 2 or not digit:
        return False
    letters = [c for c in core if c.isalpha()]
    if letters and sum(c in _VOWELS for c in letters) / len(letters) > 0.35:  # words, CamelCase
        return False
    return shannon(core) >= (ENTROPY_MIN if classes == 3 else ENTROPY_MIN_TWO_CLASS)


def _has_hint(p: Pattern, text: str, low: str | None) -> bool:
    if p.hint is None:
        return True
    if isinstance(p.hint, re.Pattern):
        return p.hint.search(text) is not None
    hay = low if p.lower else text
    return any(h in hay for h in p.hint)


def _apply(p: Pattern, text: str, counts: Counter) -> str:
    low = text.lower() if p.lower else None
    if not _has_hint(p, text, low):
        return text
    if p.lower and len(low) != len(text):  # non-ASCII case folding changed offsets
        low = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
    hay = low if p.lower else text
    out, last = [], 0
    for m in p.rx.finditer(hay):
        s = m.start()
        if p.lb and s > 0 and (text[s - 1].isalnum() or text[s - 1] == "_"):
            continue
        if p.group is not None:
            a, b = m.span(p.group)
            if p.type == "password" and _CODE_VALUE.match(text[a:b]):
                continue
        else:
            a, b = m.span()
        if a < last:
            continue
        out.append(text[last:a])
        out.append(MARK.format(p.type))
        last = b
        counts[p.type] += 1
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


def _decoded_pass(run: str, counts: Counter) -> str:
    """Pass 1 over one percent-decoded run. A run with no hit is left exactly as it was."""
    plain = unquote_plus(run)
    if plain == run:
        return run
    found: Counter = Counter()
    for p in PATTERNS:
        plain = _apply(p, plain, found)
    if not found:
        return run
    counts.update(found)
    return plain


def redact(text: str | None, counts: Counter | None = None, entropy: bool = True) -> str | None:
    """Return text with secrets replaced. Adds per-type hit counts to ``counts``."""
    if not text:
        return text
    if counts is None:
        counts = Counter()
    for p in PATTERNS:
        text = _apply(p, text, counts)
    if "%" in text:
        text = _PCT_RUN.sub(lambda m: _decoded_pass(m.group(0), counts), text)
    if entropy and len(text) >= 32:
        def ent(m: re.Match) -> str:
            if is_entropy_secret(m.group(0)):
                counts["high_entropy"] += 1
                return MARK.format("high_entropy")
            return m.group(0)
        text = _CANDIDATE.sub(ent, text)
    return text


def find_secrets(text: str | None, entropy: bool = False) -> Counter:
    """Count what would be redacted in ``text`` (regex set by default). No values returned."""
    c: Counter = Counter()
    if text:
        redact(text, c, entropy=entropy)
    return c


def types() -> list[str]:
    return [p.type for p in PATTERNS] + ["high_entropy"]
