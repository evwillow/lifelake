"""Google Takeout tree: My Activity (JSON and HTML), Chrome, Location History, calendars and mail."""
from __future__ import annotations

import base64
import html
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import text as tx
from .core import (
    HOME,
    MONTHS,
    SNAPSHOT,
    WEEKDAYS,
    Planter,
    Tree,
    Zipf,
    categorical,
    in_trip,
    iso_ms,
    lognormal,
    moments,
    new_uuid,
    rand_str,
    scaled,
    span,
    to_us,
    zone_on,
)
from .params import PARAMS

ROOT = "takeout/Takeout"


def _activity(rng: random.Random, plant: Planter, scale: float, sites: Zipf) -> tuple[list[dict], list[dict]]:
    search, youtube = [], []
    seen: set[tuple] = set()
    for t in moments(rng, scaled("n_search", scale)):
        if rng.random() < PARAMS["search_visit"]:
            host = sites.draw(rng)
            title = "Visited " + host.removeprefix("www.") + " " + rng.choice(tx.SITE_WORDS)
            url = f"https://{host}/{rng.choice(tx.SITE_WORDS)}"
        else:
            term = rng.choice(tx.SEARCH_TERMS)
            title = "Searched for " + term
            url = "https://www.google.com/search?q=" + quote_plus(term)
        title = plant.field("takeout.search", title, single_line=True)
        key = (iso_ms(t), title)
        if key not in seen:
            seen.add(key)
            search.append({"header": "Search", "title": title, "titleUrl": url, "time": iso_ms(t),
                           "products": ["Search"], "activityControls": ["Web & App Activity"], "_t": t})
    for t in moments(rng, scaled("n_youtube", scale)):
        title = plant.field("takeout.youtube", "Watched " + rng.choice(tx.VIDEO_TOPICS), single_line=True)
        key = (iso_ms(t), title)
        if key not in seen:
            seen.add(key)
            youtube.append({"header": "YouTube", "title": title,
                            "titleUrl": "https://www.youtube.com/watch?v=" + rand_str(rng, 11),
                            "subtitles": [{"name": rng.choice(tx.CHANNELS)}], "time": iso_ms(t),
                            "products": ["YouTube"], "activityControls": ["YouTube watch history"], "_t": t})
    return search, youtube


def _write_activity_json(tree: Tree, rel: str, items: list[dict]) -> None:
    last = max(i["_t"] for i in items)
    # Newest first, as real My Activity files are.
    body = [{k: v for k, v in i.items() if k != "_t"} for i in sorted(items, key=lambda x: x["_t"], reverse=True)]
    tree.write_json(rel, body, last)


def _html_time(rng: random.Random, t: datetime) -> str:
    """Times print in one fixed account zone, with its abbreviation, as My Activity HTML does."""
    loc = t.astimezone(HOME)
    h12 = loc.hour % 12 or 12
    ampm = "AM" if loc.hour < 12 else "PM"
    sep = " " if rng.random() < PARAMS["html_narrow_nbsp"] else " "
    return (f"{MONTHS[loc.month - 1]} {loc.day}, {loc.year}, {h12}:{loc.minute:02}:{loc.second:02}{sep}{ampm} "
            f"{loc.tzname()}")


def _write_maps_html(rng, tree: Tree, plant: Planter, scale: float) -> tuple[int, list[datetime]]:
    cells, times = [], []
    seen: set[tuple] = set()
    for t in moments(rng, scaled("n_maps", scale)):
        t = t.replace(microsecond=0)
        place = plant.field("takeout.maps", rng.choice(tx.PLACES), single_line=True)
        if (t, place) in seen:
            continue
        seen.add((t, place))
        href = "https://www.google.com/maps/search/" + quote_plus(place)
        body = (f'Searched for <a href="{html.escape(href)}">{html.escape(place)}</a><br>{_html_time(rng, t)}')
        cap = ""
        if rng.random() < PARAMS["html_location"]:
            lat, lon = tx.CITIES["trip" if in_trip(t.date()) else "home"]
            cap = ('<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
                   'Locations: General area '
                   f'<a href="https://www.google.com/maps/@?center={lat + rng.gauss(0, 0.02):.6f},'
                   f'{lon + rng.gauss(0, 0.02):.6f}">here</a></div>')
        cells.append('<div class="outer-cell mdl-cell"><div class="header-cell">'
                     '<p class="mdl-typography--title">Maps</p></div>'
                     f'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">{body}</div>'
                     f'{cap}</div>')
        times.append(t)
    tree.write(f"{ROOT}/My Activity/Maps/MyActivity.html", "<html><body>" + "".join(cells) + "</body></html>\n",
               max(times))
    return len(cells), times


def _write_chrome(rng, tree: Tree, plant: Planter, scale: float, sites: Zipf) -> tuple[Counter, list[datetime]]:
    domains: Counter = Counter()
    rows, times, seen = [], [], set()
    for t in moments(rng, scaled("n_chrome", scale)):
        host = sites.draw(rng)
        word = rng.choice(tx.SITE_WORDS)
        path = f"/{word}/{rng.randrange(1000)}"
        url = plant.url("takeout.chrome_url", "https", host, path)
        title = plant.field("takeout.chrome_title", f"{word.capitalize()} {rng.randrange(1000)}", single_line=True)
        if (t, url, title) in seen:
            continue
        seen.add((t, url, title))
        domains[host.removeprefix("www.")] += 1
        times.append(t)
        rows.append({"favicon_url": f"https://{host}/favicon.ico", "page_transition": "LINK", "title": title,
                     "client_id": categorical(rng, PARAMS["chrome_devices"]), "url": url, "time_usec": to_us(t)})
    tree.write_json(f"{ROOT}/Chrome/History.json", {"Browser History": rows}, max(times))
    return domains, times


def _write_location(rng, tree: Tree, plant: Planter, scale: float) -> tuple[int, list[datetime]]:
    rows, times = [], []
    sd = PARAMS["location_sd_degrees"]
    for t in moments(rng, scaled("n_location", scale)):
        lat, lon = tx.CITIES["trip" if in_trip(t.date()) else "home"]
        r = {"latitudeE7": int((lat + rng.gauss(0, sd)) * 1e7), "longitudeE7": int((lon + rng.gauss(0, sd)) * 1e7),
             "accuracy": int(lognormal(rng, PARAMS["location_accuracy"])),
             "source": categorical(rng, PARAMS["location_sources"]), "deviceTag": 12345}
        note = plant.field("takeout.location", "", single_line=True)
        if note:
            r["note"] = note
        if rng.random() < PARAMS["location_legacy_ms"]:
            r["timestampMs"] = str(to_us(t) // 1000)
        else:
            r["timestamp"] = iso_ms(t)
        rows.append(r)
        times.append(t)
    tree.write_json(f"{ROOT}/Location History/Records.json", {"locations": rows}, max(times))
    return len(rows), times


def _fold(line: str) -> str:
    """RFC 5545 line folding at 73 characters."""
    parts = [line[i:i + 73] for i in range(0, len(line), 73)]
    return "\r\n ".join(parts)


def _write_calendars(rng, tree: Tree, plant: Planter, scale: float) -> tuple[int, list[datetime]]:
    files: dict[str, list[str]] = {f"{tx.PERSON_NAME}.ics": [], "Garden Club.ics": []}
    ends = dict.fromkeys(files, SNAPSHOT - timedelta(days=366))
    times = []
    for t in moments(rng, scaled("n_calendar", scale)):
        t = t.replace(microsecond=0, second=0)
        name = "Garden Club.ics" if rng.random() < 0.3 else f"{tx.PERSON_NAME}.ics"
        loc = t.astimezone(zone_on(t.date()))
        lines = ["BEGIN:VEVENT", f"UID:{new_uuid(rng)}@example.com"]
        draw = rng.random()
        if draw < PARAMS["calendar_all_day"]:
            lines.append(f"DTSTART;VALUE=DATE:{loc.year:04}{loc.month:02}{loc.day:02}")
            start = datetime(loc.year, loc.month, loc.day, tzinfo=timezone.utc)
        elif draw < PARAMS["calendar_all_day"] + PARAMS["calendar_utc"]:
            lines.append(f"DTSTART:{t.year:04}{t.month:02}{t.day:02}T{t.hour:02}{t.minute:02}00Z")
            start = t
        else:
            lines.append(f"DTSTART;TZID={loc.tzinfo.key}:{loc.year:04}{loc.month:02}{loc.day:02}T"
                         f"{loc.hour:02}{loc.minute:02}00")
            start = t
        summary = plant.field("takeout.calendar", rng.choice(tx.CALENDAR_TITLES), single_line=True)
        lines += [f"SUMMARY:{summary}", f"DESCRIPTION:{tx.fill(rng, rng.choice(tx.MAIL_BODIES))}", "END:VEVENT"]
        files[name].extend(_fold(x) for x in lines)
        ends[name] = max(ends[name], start)
        times.append(start)
    n = len(times)
    for name, body in files.items():
        head = "BEGIN:VCALENDAR\r\nPRODID:-//Demo//Calendar//EN\r\nVERSION:2.0\r\n"
        doc = head + "\r\n".join(body + ["END:VCALENDAR"])
        tree.write(f"{ROOT}/Calendar/{name}", doc + "\r\n", ends[name])
    return n, times


def _mail_date(t: datetime) -> str:
    loc = t.astimezone(zone_on(t.date()))
    off = loc.utcoffset()
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return (f"{WEEKDAYS[loc.weekday()]}, {loc.day:02} {MONTHS[loc.month - 1]} {loc.year} "
            f"{loc.hour:02}:{loc.minute:02}:{loc.second:02} {sign}{mins // 60:02}{mins % 60:02}")


def _write_mbox(rng, tree: Tree, plant: Planter, scale: float) -> dict:
    me = f"{tx.PERSON_NAME} <{tx.PERSON_EMAIL}>"
    chunks, times = [], []
    domains: Counter = Counter()
    sent = 0
    seen = set()
    for t in moments(rng, scaled("n_email", scale)):
        t = t.replace(microsecond=0)
        is_sent = rng.random() < PARAMS["email_sent"]
        peer = rng.choice(tx.CONTACTS)
        frm, to = (me, peer) if is_sent else (peer, me)
        labels = rng.choice(tx.MAIL_LABELS_OUT if is_sent else tx.MAIL_LABELS_IN)
        subject = plant.field("takeout.email_subject", tx.fill(rng, rng.choice(tx.SUBJECTS)), single_line=True)
        plain = tx.compose(rng, tx.MAIL_BODIES, lognormal(rng, PARAMS["email_chars"]))
        body = plant.field("takeout.email_body", plain)
        if (t, subject, body) in seen:
            continue
        seen.add((t, subject, body))
        head = (f"From {tx.PERSON_EMAIL} {WEEKDAYS[t.weekday()]} {MONTHS[t.month - 1]} {t.day:02} "
                f"{t.hour:02}:{t.minute:02}:{t.second:02} +0000 {t.year}\n"
                f"From: {frm}\nTo: {to}\nSubject: {subject}\nDate: {_mail_date(t)}\n"
                f"Message-ID: <{new_uuid(rng)}@example.com>\nX-Gmail-Labels: {labels}\n")
        encoded = body == plain and rng.random() < PARAMS["email_base64"]
        payload = base64.b64encode(body.encode()).decode() if encoded else body
        cte = "Content-Transfer-Encoding: base64\n" if encoded else ""
        if rng.random() < PARAMS["email_multipart"]:
            b = rand_str(rng, 20, "0123456789abcdef")
            msg = (head + f'Content-Type: multipart/alternative; boundary="{b}"\n\n'
                   f'--{b}\nContent-Type: text/plain; charset="UTF-8"\n{cte}\n{payload}\n'
                   f'--{b}\nContent-Type: text/html; charset="UTF-8"\n\n<p>See the plain text part.</p>\n--{b}--\n')
        else:
            msg = head + f'Content-Type: text/plain; charset="UTF-8"\n{cte}\n{payload}\n'
        chunks.append(msg + "\n")
        times.append(t)
        sent += is_sent
        domains[frm.split("@")[1].rstrip(">")] += 1
    tree.write(f"{ROOT}/Mail/All mail Including Spam and Trash.mbox", "".join(chunks), max(times))
    return {"takeout_email": len(times), "takeout_email_sent": sent,
            "takeout_email_sender_domains": dict(sorted(domains.items())), "takeout_email_ts_range": span(times)}


def write_takeout(rng: random.Random, tree: Tree, plant: Planter, scale: float) -> dict:
    sites = Zipf(tx.SITES, PARAMS["site_zipf"])
    search, youtube = _activity(rng, plant, scale, sites)
    _write_activity_json(tree, f"{ROOT}/My Activity/Search/MyActivity.json", search)
    _write_activity_json(tree, f"{ROOT}/My Activity/YouTube/MyActivity.json", youtube)
    n_maps, maps_times = _write_maps_html(rng, tree, plant, scale)
    domains, chrome_times = _write_chrome(rng, tree, plant, scale, sites)
    n_loc, loc_times = _write_location(rng, tree, plant, scale)
    n_cal, cal_times = _write_calendars(rng, tree, plant, scale)
    # A file the connector does not recognize, present in every real export.
    tree.write(f"{ROOT}/archive_browser.html", "<html><body>Archive index</body></html>\n", SNAPSHOT)
    truth = {
        "takeout_search": len(search), "takeout_youtube": len(youtube), "takeout_maps": n_maps,
        "takeout_chrome": len(chrome_times), "takeout_location": n_loc, "takeout_calendar": n_cal,
        "takeout_chrome_domains": dict(sorted(domains.items())),
        "takeout_search_ts_range": span(i["_t"] for i in search),
        "takeout_youtube_ts_range": span(i["_t"] for i in youtube),
        "takeout_maps_ts_range": span(maps_times), "takeout_chrome_ts_range": span(chrome_times),
        "takeout_location_ts_range": span(loc_times), "takeout_calendar_ts_range": span(cal_times),
        "takeout_files": 8,
    }
    truth.update(_write_mbox(rng, tree, plant, scale))
    return truth

