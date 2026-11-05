"""Distributions, the calendar, the fake-secret planter and the deterministic file writer."""
from __future__ import annotations

import json
import math
import os
import random
import string
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import accumulate
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .params import PARAMS

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FIRST_DAY = date.fromisoformat(PARAMS["first_day"])
LAST_DAY = date.fromisoformat(PARAMS["last_day"])
TRIP_FIRST = date.fromisoformat(PARAMS["trip_first"])
TRIP_LAST = date.fromisoformat(PARAMS["trip_last"])
HOME = ZoneInfo(PARAMS["home_tz"])
TRIP = ZoneInfo(PARAMS["trip_tz"])
SNAPSHOT = datetime.fromisoformat(PARAMS["snapshot"])
DAYS = [FIRST_DAY + timedelta(days=i) for i in range((LAST_DAY - FIRST_DAY).days + 1)]
# A stream stops adding records here, so a late session cannot run into the next year.
HARD_END = datetime(2024, 12, 31, 12, tzinfo=UTC)

# Random strings use no vowels, so they cannot spell a word by accident.
_NO_VOWELS = "".join(c for c in string.ascii_letters if c not in "aeiouAEIOU")
ALNUM = _NO_VOWELS + string.digits
UPPER_ALNUM = "".join(c for c in ALNUM if c.isupper() or c.isdigit())
LOWER_ALNUM = "".join(c for c in ALNUM if c.islower() or c.isdigit())
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# --- distributions ------------------------------------------------------------------------

def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; large rates are split so exp(-lam) cannot underflow."""
    if lam <= 0:
        return 0
    if lam > 30:
        half = lam / 2
        return poisson(rng, half) + poisson(rng, half)
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def lognormal(rng: random.Random, params: tuple[float, float]) -> float:
    return rng.lognormvariate(params[0], params[1])


def categorical(rng: random.Random, table) -> str:
    names, weights = zip(*table, strict=True)
    return rng.choices(names, weights)[0]


class Zipf:
    """Rank-frequency sampler over a fixed list: P(rank r) is proportional to r ** -s."""

    def __init__(self, items, s: float):
        self.items = list(items)
        self.cum = list(accumulate(1 / r ** s for r in range(1, len(self.items) + 1)))

    def draw(self, rng: random.Random):
        return rng.choices(self.items, cum_weights=self.cum)[0]


def new_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def rand_str(rng: random.Random, n: int, alphabet: str = ALNUM) -> str:
    return "".join(rng.choices(alphabet, k=n))


def scaled(name: str, scale: float) -> int:
    return max(1, round(PARAMS[name] * scale))


# --- calendar -----------------------------------------------------------------------------

def in_trip(d: date) -> bool:
    return TRIP_FIRST <= d <= TRIP_LAST


def zone_on(d: date) -> ZoneInfo:
    return TRIP if in_trip(d) else HOME


def _p_active(d: date) -> float:
    return PARAMS["active_weekend"] if d.weekday() >= 5 else PARAMS["active_weekday"]


def _rate_mult(d: date) -> float:
    return PARAMS["trip_rate"] if in_trip(d) else 1.0


def local_hour(rng: random.Random) -> float:
    """One draw from the two-component normal mixture, wrapped into 0..24."""
    table = PARAMS["hour_mixture"]
    _w, mu, sd = rng.choices(table, [c[0] for c in table])[0]
    return rng.gauss(mu, sd) % 24.0


def moment(rng: random.Random, day: date) -> datetime:
    """A UTC instant on a local day, at a mixture-drawn hour, in the zone the persona is in that day."""
    h = local_hour(rng)
    hh = int(h)
    mm = int((h - hh) * 60)
    naive = datetime(day.year, day.month, day.day, hh, mm, rng.randrange(60), rng.randrange(1000) * 1000)
    return naive.replace(tzinfo=zone_on(day)).astimezone(UTC)


def moments(rng: random.Random, target: int) -> list[datetime]:
    """Sorted instants: a Bernoulli-active day, then Poisson per active day, rate set to hit ``target``."""
    base = target / sum(_p_active(d) * _rate_mult(d) for d in DAYS)
    out: list[datetime] = []
    for d in DAYS:
        if rng.random() >= _p_active(d):
            continue
        for _ in range(poisson(rng, base * _rate_mult(d))):
            out.append(moment(rng, d))
    if not out:  # a tiny scale can draw nothing; every stream keeps one record
        out.append(moment(rng, DAYS[0]))
    return sorted(out)


def lake_zone(ts: datetime) -> ZoneInfo:
    """The zone the lake assigns to an instant: travel bounds there are UTC dates."""
    return TRIP if in_trip(ts.astimezone(UTC).date()) else HOME


# --- time formats -------------------------------------------------------------------------

def iso_ms(ts: datetime) -> str:
    t = ts.astimezone(UTC)
    return f"{t.year:04}-{t.month:02}-{t.day:02}T{t.hour:02}:{t.minute:02}:{t.second:02}.{t.microsecond // 1000:03}Z"


def iso_s(ts: datetime) -> str:
    t = ts.astimezone(UTC)
    return f"{t.year:04}-{t.month:02}-{t.day:02}T{t.hour:02}:{t.minute:02}:{t.second:02}Z"


def to_ns(ts: datetime) -> int:
    return (ts - EPOCH) // timedelta(microseconds=1) * 1000


def to_us(ts: datetime) -> int:
    return (ts - EPOCH) // timedelta(microseconds=1)


def span(values) -> list[str] | None:
    """[first, last] as ISO strings, for comparison with min() and max() in SQL."""
    vals = list(values)
    return [min(vals).isoformat(), max(vals).isoformat()] if vals else None


# --- fake secrets -------------------------------------------------------------------------
# Prefixes are split across string pieces so that no literal in this file looks like a credential.

def _mk_anthropic(r):
    return "sk-" + "ant-" + "api03-" + rand_str(r, 60) + "AA"


def _mk_openai(r):
    return "sk-" + "proj-" + rand_str(r, 48)


def _mk_github(r):
    return "gh" + "p_" + rand_str(r, 36)


def _mk_aws_access(r):
    return "AK" + "IA" + rand_str(r, 16, UPPER_ALNUM)


def _mk_aws_secret(r):
    return rand_str(r, 40, LOWER_ALNUM + "/+")


def _mk_stripe(r):
    return "sk" + "_live_" + rand_str(r, 24)


def _mk_google(r):
    return "AI" + "za" + rand_str(r, 35)


def _mk_slack(r):
    return "xo" + "xb-" + str(r.randrange(10 ** 11, 10 ** 12)) + "-" + rand_str(r, 24)


def _mk_telegram(r):
    return str(r.randrange(10 ** 8, 10 ** 10)) + ":" + "AA" + rand_str(r, 33)


def _mk_jwt(r):
    return "ey" + "J" + rand_str(r, 20) + ".ey" + "J" + rand_str(r, 40) + "." + rand_str(r, 43)


def _mk_url_password(r):
    return rand_str(r, 14)


def _mk_broker(r):
    return r.choice(("U", "DU")) + str(r.randrange(10 ** 6, 10 ** 7))


def _mk_password(r):
    return rand_str(r, 10) + str(r.randrange(10, 100)) + r.choice("!#%&?")


def _mk_high_entropy(r):
    # A digit is forced so the token always passes the entropy test's mixed-class rule.
    return rand_str(r, 19, _NO_VOWELS) + str(r.randrange(10)) + rand_str(r, 20)


_PEM = "RSA PRIVATE " + "KEY-----"


def _mk_private_key(r):
    lines = [rand_str(r, 64) for _ in range(4)]
    return "-----BEGIN " + _PEM + "\n" + "\n".join(lines) + "\n-----END " + _PEM


# kind -> (maker, phrases that carry the secret)
KINDS = {
    "anthropic_key": (_mk_anthropic, ("export ANTHROPIC_API_KEY={s}", "the key is {s} for now")),
    "openai_key": (_mk_openai, ("OPENAI_API_KEY={s}", "use {s} for the test run")),
    "github_token": (_mk_github, ("GH_TOKEN={s} gh pr list", "token: {s}")),
    "aws_access_key": (_mk_aws_access, ("AWS_ACCESS_KEY_ID={s}", "access id {s} in the config")),
    "aws_secret_key": (_mk_aws_secret, ("aws_secret_access_key = {s}", "AWS_SECRET_ACCESS_KEY={s}")),
    "stripe_key": (_mk_stripe, ("STRIPE_SECRET_KEY={s}", "billing key {s}")),
    "google_api_key": (_mk_google, ("GOOGLE_API_KEY={s}", "maps key {s}")),
    "slack_token": (_mk_slack, ("SLACK_BOT_TOKEN={s}", "Authorization: Bearer {s}")),
    "telegram_bot_token": (_mk_telegram, ("TELEGRAM_BOT_TOKEN={s}", "bot = Bot({s})")),
    "jwt": (_mk_jwt, ("Authorization: Bearer {s}", "jwt: {s}")),
    "url_credentials": (_mk_url_password, ("postgres://app:{s}@db.example.com:5432/app",
                                           "redis://default:{s}@cache.example.org:6379")),
    "broker_account": (_mk_broker, ("broker account {s}", "accountId={s}")),
    "password": (_mk_password, ("password={s}", "the db password: {s}")),
    "high_entropy": (_mk_high_entropy, ("X-Demo-Token: {s}", "license {s} expires soon")),
    "private_key": (_mk_private_key, ("key:\n{s}\n", "{s}")),
}
MULTI_LINE = frozenset({"private_key"})
# Fields the connectors redact without the entropy pass cannot carry a high_entropy secret.
NO_ENTROPY = frozenset({"high_entropy"})


class Planter:
    """Adds fake secrets to text fields and counts them. ``values`` lets a test look them up later."""

    def __init__(self, rng: random.Random, values: list[str] | None = None):
        self.rng = rng
        self.by_type: Counter = Counter()
        self.by_field: Counter = Counter()
        self.values = values if values is not None else []
        self._order = list(KINDS)
        rng.shuffle(self._order)
        self._next = 0
        self._rates = PARAMS["secret_rates"]

    def _kind(self, exclude: frozenset) -> str:
        """Cycle through the kinds, so even a small demo shows many of them."""
        for _ in range(len(self._order)):
            k = self._order[self._next % len(self._order)]
            self._next += 1
            if k not in exclude:
                return k
        raise ValueError("every secret kind is excluded")

    def _hit(self, label: str) -> bool:
        return self.by_field[label] == 0 or self.rng.random() < self._rates[label]

    def _record(self, label: str, kind: str, value: str) -> None:
        self.by_type[kind] += 1
        self.by_field[label] += 1
        # For a PEM block the first body line stands in for the whole value.
        self.values.append(value.split("\n")[1] if kind in MULTI_LINE else value)

    def field(self, label: str, text: str, single_line: bool = False, entropy: bool = True) -> str:
        """Return ``text``, with a phrase holding a fake secret appended if the Bernoulli draw says so."""
        if not self._hit(label):
            return text
        exclude = frozenset()
        if single_line:
            exclude |= MULTI_LINE
        if not entropy:
            exclude |= NO_ENTROPY
        kind = self._kind(exclude)
        maker, phrases = KINDS[kind]
        value = maker(self.rng)
        self._record(label, kind, value)
        return (text + " " if text else "") + self.rng.choice(phrases).format(s=value)

    def url(self, label: str, scheme: str, host: str, path: str) -> str:
        """A URL, with credentials embedded in its authority if the Bernoulli draw says so."""
        if not self._hit(label):
            return f"{scheme}://{host}{path}"
        value = _mk_url_password(self.rng)
        self._record(label, "url_credentials", value)
        return f"{scheme}://admin:{value}@{host}{path}"

    def by_source(self) -> dict[str, int]:
        out: Counter = Counter()
        for label, n in self.by_field.items():
            out[label.split(".")[0]] += n
        return dict(sorted(out.items()))


# --- files --------------------------------------------------------------------------------

class Tree:
    """Writes files under a root and, at the end, stamps each one with its last record's synthetic time."""

    def __init__(self, root: Path):
        self.root = root
        self._times: dict[Path, datetime] = {}

    def _path(self, rel: str, last: datetime) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        self._times[p] = last
        return p

    def write(self, rel: str, data: str | bytes, last: datetime) -> None:
        self._path(rel, last).write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def write_json(self, rel: str, obj, last: datetime) -> None:
        self.write(rel, json.dumps(obj, ensure_ascii=False, indent=1) + "\n", last)

    def write_parquet(self, rel: str, table: pa.Table, last: datetime) -> None:
        # Every setting that affects the bytes is pinned, so equal rows give equal files.
        pq.write_table(table, self._path(rel, last), compression="zstd", compression_level=3, use_dictionary=True,
                       write_statistics=True, version="2.6", data_page_version="1.0", row_group_size=20_000,
                       store_schema=True)

    def finish(self) -> None:
        dirs: dict[Path, datetime] = {}
        for p, ts in self._times.items():
            ns = to_ns(ts)
            os.utime(p, ns=(ns, ns))
            for d in p.parents:
                if d == self.root.parent:
                    break
                if d not in dirs or ts > dirs[d]:
                    dirs[d] = ts
        for d in sorted(dirs, key=lambda x: -len(x.parts)):
            ns = to_ns(dirs[d])
            os.utime(d, ns=(ns, ns))
