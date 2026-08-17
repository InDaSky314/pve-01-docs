#!/usr/bin/env python3
"""Media-Core Xtream sync (v3).

Pulls from the provider's Xtream API (get.php M3U download is disabled on
this panel) and generates:
  1. threadfin/conf/playlist.m3u  — live channels from ordered, grouped
     selections in config.json (playlist order = guide order)
  2. epg/epg.xml                  — provider XMLTV filtered to those
     channels, plus a <channel> entry with logo for EVERY channel, plus
     programmes merged from external XMLTV sources (epgshare01) for
     channels the provider carries no guide data for (matched by call
     sign or normalized name, per-selection "epg_region")
  3. media/movies/*/*.strm        — VOD movie links, titles normalized to
     "Title (Year)" for reliable TMDB artwork matching

v3 reliability changes:
  - all provider API calls retry 3x with backoff
  - VOD prune guard: if the provider returns fewer than 70% of the movies
    we already have, nothing is deleted (partial API responses used to
    mass-prune the library and made Jellyfin's counts fluctuate)
  - after a successful run, Jellyfin's Refresh Guide and library scan are
    triggered via API (key in /srv/media-core/.jellyfin_api_key) so the
    library converges right after the 04:00 sync, off-peak

Credentials come from /srv/media-core/.env (XTREAM_BASE/USER/PASS).
Selection lives in config.json next to this script.
Runs daily via media-core-sync.timer; run manually with:
  python3 /srv/media-core/sync/xtream-sync.py
"""
import calendar
import gzip
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import loki_alert
import channel_naming            # display-name rules, shared with the tooling

BASE_DIR = Path("/srv/media-core")
ENV_FILE = BASE_DIR / ".env"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
PLAYLIST_OUT = BASE_DIR / "threadfin/conf/playlist.m3u"
EPG_OUT = BASE_DIR / "epg/epg.xml"
MOVIES_DIR = BASE_DIR / "media/movies"
SHOWS_DIR = BASE_DIR / "media/shows"
SERIES_CACHE = Path(__file__).resolve().parent / "cache" / "series"
JF_KEY_FILE = BASE_DIR / ".jellyfin_api_key"
JF_URL = "http://127.0.0.1:8096"
UA = "MediaCoreSync/1.0"

# Curated channel artwork, served from the Proxmox host by icon-host.service.
# Preferred over the provider's stream_icon wherever we have art, because the
# provider has replaced many logos with a shared placeholder. Keyed by channel
# name with : / | stripped, matching how the files are written.
ICON_HOST = "http://192.168.9.11:8100"
_ICON_STRIP = re.compile(r"[:/|]")


def icon_overrides():
    """display name -> local icon url. Empty dict if the host is unreachable."""
    try:
        req = urllib.request.Request(f"{ICON_HOST}/index.json",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            cat = json.load(r)
    except Exception as exc:                                  # noqa: BLE001
        print(f"WARNING: icon host unreachable ({exc}) — using provider logos")
        return {}
    return {name: ICON_HOST + path for name, path in cat.items()}


def pick_logo(disp, stream_icon, overrides):
    """Curated art if we have it, else whatever the provider offers."""
    return overrides.get(_ICON_STRIP.sub("", disp).strip()) or stream_icon or ""

PRUNE_GUARD = 0.70  # refuse to prune if VOD list shrank below this ratio

# Service-branded categories (2026-07-18) get their own library instead of
# folding into the general Movies/Series pool — a title can legitimately
# land in more than one (e.g. on both Netflix and Amazon), each deduped
# independently, same HD-before-4K/EN-first priority as the general pool.
# Matched by category-name prefix; anything unmatched stays general.
SERVICE_PATTERNS = [
    ("Netflix", re.compile(r"^NETFLIX", re.I)),
    ("Amazon", re.compile(r"^AMAZON", re.I)),
    ("Apple TV+", re.compile(r"^APPLE\+", re.I)),
    ("Disney+", re.compile(r"^DISNEY\+", re.I)),
    # second wave (2026-07-18): premium services + film studios
    ("Marvel", re.compile(r"^MARVEL", re.I)),
    ("Paramount+", re.compile(r"^PARAMOUNT\+", re.I)),
    ("Paramount", re.compile(r"^PARAMOUNT PICTURES", re.I)),
    ("Peacock", re.compile(r"^PEACOCK", re.I)),
    ("Showtime", re.compile(r"^SHOWTIME", re.I)),
    ("Sky", re.compile(r"^SKY\b", re.I)),
    ("Discovery+", re.compile(r"^DISCOVERY\+", re.I)),
    ("Crunchyroll", re.compile(r"^CRUNCHYROLL", re.I)),
    ("Nickelodeon", re.compile(r"^NICKELODEON", re.I)),
    ("Universal", re.compile(r"^UNIVERSAL", re.I)),
    ("DreamWorks", re.compile(r"^DREAMWORKS", re.I)),
    ("James Bond", re.compile(r"^JAMES BOND", re.I)),
]
SERVICE_SLUG = {"Netflix": "netflix", "Amazon": "amazon",
                "Apple TV+": "appletv", "Disney+": "disney",
                "Marvel": "marvel", "Paramount+": "paramountplus",
                "Paramount": "paramount", "Peacock": "peacock",
                "Showtime": "showtime", "Sky": "sky",
                "Discovery+": "discoveryplus", "Crunchyroll": "crunchyroll",
                "Nickelodeon": "nickelodeon", "Universal": "universal",
                "DreamWorks": "dreamworks", "James Bond": "bond"}
MOVIES_SERVICE_DIRS = {s: BASE_DIR / f"media/movies-{slug}"
                        for s, slug in SERVICE_SLUG.items()}
SHOWS_SERVICE_DIRS = {s: BASE_DIR / f"media/shows-{slug}"
                       for s, slug in SERVICE_SLUG.items()}


def category_service(cat_name):
    """Which branded library a category belongs to, or None for general."""
    for service, pat in SERVICE_PATTERNS:
        if pat.match(cat_name or ""):
            return service
    return None

# superscript/phonetic decoration chars providers love (ᴴᴰ ᴿᴬᵂ ⁶⁰ᶠᵖˢ …)
DECOR = re.compile(r"[ᴀ-᷿⁰-₟ʰ-˿]")


def read_env():
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip()
    return env


def fetch(url, timeout=180, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(1, tries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            if attempt == tries:
                raise
            print(f"WARNING: fetch failed (attempt {attempt}/{tries}): {e}")
            time.sleep(5 * attempt)


def api(base, user, pw, action):
    url = f"{base}/player_api.php?username={user}&password={pw}&action={action}"
    # the panel intermittently answers 200 with an empty list — retry those
    # like transport errors, else a bad night looks like "everything deleted"
    for attempt in range(1, 4):
        with fetch(url) as r:
            data = json.load(r)
        if data != []:
            return data
        if attempt < 3:
            print(f"WARNING: {action} returned empty "
                  f"(attempt {attempt}/3) — retrying")
            time.sleep(20 * attempt)
    return data


# Channel names as the provider ships them carry its grouping tag ("US:",
# "GO:", "DE:") and are entirely uppercase. modernise_name() strips the tag,
# applies Title Case and corrects renamed brands -- see channel_naming.py.
#
# Gated on config so it can be turned off without editing code, and so the
# change is visible in the config diff rather than buried here. Applied only
# to provider-derived names: curated names from config.json and event-slot
# names are already in house style and are passed through untouched.
def modernise_name(name):
    if not CFG_CACHE.get("modernise_names", True):
        return name
    return channel_naming.modernise(name)


CFG_CACHE = {}


def clean_channel_name(name):
    name = DECOR.sub("", name or "")
    name = name.replace(",", " ")
    # Threadfin truncates channel names at apostrophes ("CHAPPELLE'S" ->
    # "CHAPPELLE") which even collapsed VEVO '70S/'80S into one channel
    name = name.replace("'", "").replace("’", "").replace("`", "")
    return re.sub(r"\s+", " ", name).strip(" -")


SUPERSCRIPT_DIGITS = str.maketrans(
    {c: " " + d for c, d in zip("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")})


def clean_movie_title(name):
    # "The Accountant²" == "The Accountant 2" for TMDB purposes
    name = (name or "").translate(SUPERSCRIPT_DIGITS)
    name = DECOR.sub("", name)
    # Strip provider prefixes like "EN - ", "4K-EN - ", "EN-TOP - ", and the
    # newer streaming-service tags this originally missed: "D+ - ", "A+ - ",
    # "PRMT - ", "MRVL - ", "4K-MRVL - ", "ËN - ", "(TS) - ". Root-caused
    # 2026-08-17 (Agy investigation + my own verification): the old pattern
    # only matched 2-3 plain uppercase letters, so it silently left these
    # newer prefixes in place, TMDB then found 0 search results for the
    # polluted title, and 828 of 1245 no-artwork movies (66.5%) turned out
    # to be exactly this -- Jellyfin WAS processing the refresh request
    # (DateLastRefreshed got set), it just never found a match to attach.
    # Widened to 1-6 letters (covers single-letter D/A) with an optional
    # trailing '+', plus accented uppercase (Ë) and a separate (TS)/(CAM)/
    # (HDCAM) leader for cam-rip tags. Deliberately requires \s+-\s+ (real
    # spaces on both sides of the dash), not \s*-\s* -- the loose version
    # would have eaten real hyphenated titles like "X-Men" or "K-19" that
    # have no surrounding whitespace. Verified against 300 real random
    # titles from the live library plus every "+"-containing real title
    # found (Romeo + Juliet, Flesh + Blood, Ivy + Bean, etc.) -- zero false
    # strips beyond the one genuine (TS)-prefixed title in the sample.
    name = re.sub(
        r"^(?:[0-9]K-)?[A-ZÀ-Ý]{1,6}\+?(?:-[A-ZÀ-Ý]{1,6}\+?)?\s+-\s+"
        r"|^\((?:TS|CAM|HDCAM)\)\s+-?\s*",
        "", name)
    name = re.sub(r"^\d{1,4}\.\s+", "", name)   # list numbering "248. Title"
    # scene-style dotted names: "123.L.A.Confidential.1997"
    if " " not in name and name.count(".") >= 2:
        name = re.sub(r"^\d{1,4}\.", "", name)
        name = name.replace(".", " ")
        name = re.sub(r"^(.+) (\d{4})$", r"\1 (\2)", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:150] or "unnamed"


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode) as f:
        f.write(data)
    tmp.replace(path)


def slot_display(raw, label):
    """Stable display name for event-slot channels ("MLB 12", "UEFA 04").

    PPV slot names carry the current event ("MLB 12 | Brewers x Cardinals
    start:…") and change all day; Threadfin keys channels by name, so the
    playlist must use the invariant slot part. Returns None for names that
    don't look like a slot (e.g. "MLB: MLB NETWORK" league channels).
    """
    clean = clean_channel_name(raw)
    m = re.search(rf"{re.escape(label)}\s*[|:\- ]*(\d{{1,3}}|4K)\b", clean, re.I)
    if not m:
        return None
    num = m.group(1).upper()
    return f"{label} {num.zfill(2) if num.isdigit() else num}"


def resolve_xid(s, claimed_epgids):
    """Provider epg_channel_id, unless another stream already claimed it.

    Regional opt-out feeds (WDR/RBB/SWR variants, local CW affiliates,
    etc.) are frequently given an identical epg_channel_id by the
    provider despite being distinct streams — first one processed
    silently wins the guide slot in `emitted` and the rest get no
    <channel> entry at all under their own name. Falling back to a
    per-stream id for the loser lets it flow through the normal
    external-match/synth pipeline instead of vanishing.
    """
    epgid = s.get("epg_channel_id")
    if epgid and epgid not in claimed_epgids:
        claimed_epgids.add(epgid)
        return epgid
    return f"mc{s['stream_id']}"


def build_playlist(base, user, pw, cfg):
    cats = api(base, user, pw, "get_live_categories")
    id2name = {c["category_id"]: c["category_name"] for c in cats}
    streams = api(base, user, pw, "get_live_streams")
    by_cat = {}
    by_id = {}
    for s in streams:
        by_cat.setdefault(s.get("category_id"), []).append(s)
        try:
            by_id[int(s["stream_id"])] = s
        except (TypeError, ValueError):
            pass

    exclude = re.compile(cfg.get("live_name_exclude", "$^"), re.I)
    chosen = []   # (stream, group_label, xmltv_id, display_name, region, chno, epg_mode)
    seen_ids = set()
    seen_names = set()   # (group, display name): drop exact duplicate entries
    claimed_epgids = set()
    group_counts = {}
    next_chno = 0
    for sel in cfg["live_selections"]:
        region = sel.get("epg_region")
        mode = sel.get("epg_mode")
        slot = sel.get("slot")
        next_chno = sel.get("start_chno") or next_chno

        # explicit stream-id selections (v7 owner picks): listed order is
        # channel order; robust against name churn on event/PPV slots
        if sel.get("ids"):
            missing = 0
            id_list = sel["ids"].keys() if isinstance(sel["ids"], dict) else sel["ids"]
            for sid in id_list:
                s = by_id.get(int(sid))
                if s is None:
                    missing += 1
                    continue
                if s["stream_id"] in seen_ids:
                    continue
                raw = s.get("name") or ""
                sname = slot and slot_display(raw, slot)
                if isinstance(sel["ids"], dict):
                    # curated name from config.json — already in house style
                    disp = sel["ids"][sid]
                elif sname:
                    disp = sname          # event-slot name, e.g. "Soccer PPV 01"
                else:
                    disp = modernise_name(clean_channel_name(raw))
                if (sel["group"], disp) in seen_names:
                    continue
                seen_ids.add(s["stream_id"])
                seen_names.add((sel["group"], disp))
                xid = resolve_xid(s, claimed_epgids)
                # league networks inside ppv blocks aren't event slots —
                # let them take the external/synth EPG path instead
                cmode = mode if (mode != "ppv" or sname) else None
                chosen.append((s, sel["group"], xid, disp, region, next_chno, cmode))
                next_chno += 1
                group_counts[sel["group"]] = group_counts.get(sel["group"], 0) + 1
            if missing:
                print(f"WARNING: {sel['group']}: {missing}/{len(sel['ids'])} "
                      "stream ids no longer on the panel")
            continue

        cat_rx = re.compile(sel["category"])
        name_rx = re.compile(sel["name"], re.I) if sel.get("name") else None
        nexc_rx = (re.compile(sel["name_exclude"], re.I)
                   if sel.get("name_exclude") else None)
        # numbering: each group gets a block (start_chno); channels number
        # sequentially inside it, in selection order
        matched_cat = False
        for cid, cname in id2name.items():
            if not cat_rx.search(cname):
                continue
            matched_cat = True
            for s in by_cat.get(cid, []):
                raw = s.get("name") or ""
                if s["stream_id"] in seen_ids or exclude.search(raw):
                    continue
                if name_rx and not name_rx.search(raw):
                    continue
                if nexc_rx and nexc_rx.search(raw):
                    continue
                sname = slot and slot_display(raw, slot)
                disp = sname or modernise_name(clean_channel_name(raw))
                if (sel["group"], disp) in seen_names:
                    continue
                seen_ids.add(s["stream_id"])
                seen_names.add((sel["group"], disp))
                xid = resolve_xid(s, claimed_epgids)
                cmode = mode if (mode != "ppv" or sname) else None
                chosen.append((s, sel["group"], xid, disp, region, next_chno, cmode))
                next_chno += 1
                group_counts[sel["group"]] = group_counts.get(sel["group"], 0) + 1
        if not matched_cat:
            print(f"WARNING: selection matched no category: {sel['category']}")

    # warn if a group outgrew its number block and bled into the next one
    sel_starts = sorted(s["start_chno"] for s in cfg["live_selections"]
                        if s.get("start_chno"))
    span = {}   # group -> [min chno, max chno]
    for _s, group, _x, _d, _r, chno, _m in chosen:
        lo, hi = span.get(group, (chno, chno))
        span[group] = (min(lo, chno), max(hi, chno))
    for group, (lo, hi) in span.items():
        nxt = next((st for st in sel_starts if st > lo), None)
        if nxt and hi >= nxt:
            print(f"WARNING: group {group!r} overflows its block "
                  f"(reaches {hi}, next block starts at {nxt})")
    # same rule as the VOD prune guard: a partial provider answer must not
    # replace a healthy playlist — abort and keep yesterday's lineup instead
    try:
        old_count = PLAYLIST_OUT.read_text().count("#EXTINF")
    except OSError:
        old_count = 0
    if old_count and len(chosen) < old_count * PRUNE_GUARD:
        sys.exit(f"ERROR: provider returned {len(chosen)} live channels but "
                 f"the current playlist has {old_count} (< {PRUNE_GUARD:.0%})"
                 " — keeping the existing playlist, aborting sync")
    overrides = icon_overrides()
    if overrides:
        print(f"icons: {len(overrides)} curated overrides available")
    lines = ["#EXTM3U"]
    used = 0
    for s, group, xid, disp, _region, chno, _mode in chosen:
        logo = pick_logo(disp, s.get("stream_icon"), overrides)
        if logo.startswith(ICON_HOST):
            used += 1
        lines.append(
            f'#EXTINF:-1 tvg-id="{xid}" tvg-chno="{chno}" tvg-name="{disp}" '
            f'tvg-logo="{logo}" group-title="{group}",{disp}'
        )
        lines.append(f'{base}/live/{user}/{pw}/{s["stream_id"]}.ts')
    atomic_write(PLAYLIST_OUT, "\n".join(lines) + "\n")
    if overrides:
        print(f"icons: {used} channels using curated artwork")
    print(f"playlist: {len(chosen)} channels — " +
          ", ".join(f"{g}: {n}" for g, n in group_counts.items()))
    return chosen


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# external EPG matching

QUALITY_TOKENS = {"HD", "UHD", "FHD", "SD", "4K", "8K", "RAW", "VIP",
                  "1080P", "720P", "3840P", "H265", "HEVC"}
CALLSIGN = re.compile(r"\b([WK][A-Z]{3})\b")


def norm_key(name):
    """Normalize a channel name to a comparison key."""
    s = DECOR.sub("", name or "").upper()
    s = re.sub(r"^(US|UK|DE|VIP|CA|AU)\s*[:|]\s*", "", s)   # country prefix
    s = re.sub(r"\([^)]*\)", " ", s)                        # (MOBIL), (A), …
    s = s.replace("SPORTS", "SPORT")                        # plural-insensitive
    words = [w for w in re.split(r"[^A-Z0-9]+", s) if w and w not in QUALITY_TOKENS]
    return "".join(words)


def our_match_keys(disp, aliases=None):
    """Candidate keys for one of our channels, most specific first."""
    keys = []
    alias = (aliases or {}).get(norm_key(disp))
    if alias:  # manual override from config epg_aliases
        # short alias values can coincidentally look like a US call sign
        # (e.g. "WDR" matches [WK][A-Z]{2,4}) without being one — try the
        # call-sign index first (most specific) but always also offer the
        # plain name key, so a false-positive callsign match doesn't
        # shadow the real name-based match.
        if re.fullmatch(r"[WK][A-Z]{2,4}", alias.upper()):
            keys.append(("call", alias.upper()))
        # Real bug found + fixed 2026-08-17 (verified by tracing both this
        # function and ext_channel_keys directly): an alias value like
        # "scraped.hgtv.us" hashed whole here, but ext_channel_keys()
        # below strips the trailing country suffix from the external
        # channel's own id before hashing its stem -- "scraped.hgtv.us"
        # vs "scraped.hgtv" never matched. Added the same stripped form
        # here too (purely additive -- the original full-alias key stays)
        # so aliasing actually works for the scrapers this was built for.
        alias_stem = re.sub(r"\.(us(_locals\d*|_sports\d*)?|uk|de)$", "", alias, flags=re.I)
        if alias_stem != alias:
            keys.append(("name", norm_key(alias_stem.replace(".", " "))))
        keys.append(("name", norm_key(alias)))
    m = re.search(r"\(([WK][A-Z]{3})[^)]*\)", disp.upper())  # call sign in ()
    if m:
        keys.append(("call", m.group(1)))
    for c in CALLSIGN.findall(disp.upper()):
        if ("call", c) not in keys:
            keys.append(("call", c))
    k = norm_key(disp)
    if k:
        keys.append(("name", k))
    return keys


def ext_channel_keys(chan_id, names):
    """Index keys under which an external channel is findable."""
    keys = set()
    for n in names:
        # call-sign style display names: WISN-DT, K24HQ-D, WEEK-DT2 …
        m = re.match(r"^([WK][A-Z]{2,4})-(?:DT|HD|LD|CD|D|TV)\d*$", n.upper())
        if m:
            keys.add(("call", m.group(1)))
        k = norm_key(n)
        if k:
            keys.add(("name", k))
    # the id itself is often the most descriptive: TNT.Sports.1.HD.uk
    stem = re.sub(r"\.(us(_locals\d*|_sports\d*)?|uk|de)$", "", chan_id, flags=re.I)
    k = norm_key(stem.replace(".", " "))
    if k:
        keys.add(("name", k))
    # Also index the raw (un-stripped) chan_id -- complements the
    # alias_stem fix in our_match_keys() above so a match works whether
    # the alias value in config.json was written with or without the
    # trailing country suffix.
    k_raw = norm_key(chan_id.replace(".", " "))
    if k_raw:
        keys.add(("name", k_raw))
    return keys


def merge_external_epg(out, cfg, needy):
    """Stream external XMLTV sources, copy programmes for matched channels.

    needy: {region: {xid: disp}} channels still without programmes.
    Returns set of xids that received programmes.
    """
    sources = cfg.get("external_epg", {})
    all_aliases = cfg.get("epg_aliases", {})
    fed = set()
    for region, urls in sources.items():
        want = needy.get(region) or {}
        aliases = {norm_key(k): v for k, v in all_aliases.get(region, {}).items()}
        for url in urls:
            pending = {x: d for x, d in want.items() if x not in fed}
            if not pending:
                break
            # Real bug found + fixed 2026-08-17: one bad external source
            # (timeout, HTTP 4xx/5xx, malformed XML -- any of which can
            # legitimately happen to any one of 19+ external EPG URLs on
            # any given night) used to propagate an uncaught exception
            # all the way up through build_epg() and crash the ENTIRE
            # sync -- playlist, EPG, VOD, series, and the Jellyfin
            # refresh trigger never ran, not just that one source's data
            # going missing. Isolate each source's failure instead.
            try:
                fed |= _merge_one_source(out, url, pending, aliases)
            except Exception as exc:                          # noqa: BLE001
                src_name = url.rsplit("/", 1)[-1]
                print(f"WARNING: external epg {src_name} failed ({exc}) — continuing with remaining sources")
                loki_alert.push("epg-sync", f'msg="external epg source failed" error="{exc}"',
                                level="warn", source=src_name)
    return fed


def _merge_one_source(out, url, pending, aliases=None):
    """One external XMLTV file: match `pending` {xid: disp}, write programmes."""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        with fetch(url, timeout=600) as r:
            shutil.copyfileobj(r, tmp)
        raw = Path(tmp.name)
    fed = set()
    try:
        with open(raw, "rb") as fh:
            magic = fh.read(2)
        opener = gzip.open if magic == b"\x1f\x8b" else open
        index = {}          # match key -> [external channel ids]
        mapping = None      # external id -> [candidate xids]
        binding = {}        # xid -> the external id that actually fed it
        with opener(raw, "rb") as fh:
            root = None
            for event, elem in ET.iterparse(fh, events=("start", "end")):
                if event == "start":
                    if root is None:
                        root = elem
                    continue
                if elem.tag == "channel":
                    names = [e.text or "" for e in elem.findall("display-name")]
                    for key in ext_channel_keys(elem.get("id", ""), names):
                        ids = index.setdefault(key, [])
                        if elem.get("id") not in ids:
                            ids.append(elem.get("id"))
                elif elem.tag == "programme":
                    if mapping is None:   # all <channel> elements seen by now
                        # keep every candidate: some external channels are
                        # empty shells (a <channel> entry, zero programmes) —
                        # an xid binds to the first candidate with real data
                        mapping = {}
                        for xid, disp in pending.items():
                            for key in our_match_keys(disp, aliases):
                                for ext in index.get(key, []):
                                    if xid not in mapping.setdefault(ext, []):
                                        mapping[ext].append(xid)
                    ext = elem.get("channel")
                    for xid in mapping.get(ext, ()):
                        if binding.setdefault(xid, ext) == ext:
                            elem.set("channel", xid)
                            out.write(ET.tostring(elem, encoding="utf-8"))
                            fed.add(xid)
                if elem.tag in ("channel", "programme"):
                    elem.clear()
                    if root is not None:
                        root.clear()
    finally:
        raw.unlink()
    src_name = url.rsplit('/', 1)[-1]
    print(f"external epg {src_name}: matched {len(fed)}/{len(pending)} channels")
    loki_alert.push("epg-sync",
                     f'level=info source="{src_name}" matched={len(fed)} pending={len(pending)}')
    return fed


# ---------------------------------------------------------------------------
# guide entries for channels no XMLTV source knows about

PPV_STATE = Path(__file__).resolve().parent / "cache" / "ppv-xids.json"
XMLFMT = "%Y%m%d%H%M%S +0000"


def _prog(xid, start, stop, title, desc=""):
    p = (f'<programme start="{time.strftime(XMLFMT, time.gmtime(start))}" '
         f'stop="{time.strftime(XMLFMT, time.gmtime(stop))}" '
         f'channel="{xml_escape(xid)}">'
         f'<title>{xml_escape(title)}</title>')
    if desc:
        p += f"<desc>{xml_escape(desc)}</desc>"
    return (p + "</programme>\n").encode("utf-8")


PPV_TIMED = re.compile(r"start:\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*"
                       r"stop:\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
UK_KICKOFF = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.I)


def ppv_programmes(raw, xid):
    """Guide entries parsed out of an event-slot channel name.

    Panel formats seen in the wild:
      "MLB 12 | Brewers x Cardinals start:2026-07-09 00:45:00 stop:…"  (UTC)
      "Live | Tour de France: Stage 6 | all | … | US: SOCCER PPV 3"
      "Live Football 01: Shelbourne vs Celtic 6:00pm"           (Europe/London)
      "NBA 05 :"                                                (idle slot)
    """
    name = clean_channel_name(raw)
    now = int(time.time()) // 3600 * 3600
    m = PPV_TIMED.search(name)
    if m:
        try:
            start = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            stop = calendar.timegm(time.strptime(m.group(2), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            start = stop = 0
        title = re.sub(r"^[^|:]*[|:]", "", name[:m.start()])
        title = re.sub(r"^\s*\d{1,3}\s*[|\-:]\s*", "", title).strip(" -|:")
        if stop > time.time() and title:
            return [_prog(xid, start, stop, title,
                          "Event times as published by the provider (UTC).")]
    segs = [t.strip() for t in name.split("|")]
    if len(segs) >= 2 and segs[0] in ("Live", "End"):
        if segs[0] == "Live":
            return [_prog(xid, now, now + 4 * 3600, "LIVE: " + segs[1])]
        return [_prog(xid, now, now + 24 * 3600, "No live event",
                      f"Last event: {segs[1]}")]
    m = re.match(r"^Live Football \d+\s*[:|-]\s*(\S.*)$", name, re.I)
    if m:
        title = UK_KICKOFF.sub("", m.group(1)).strip(" -|:")
        km = UK_KICKOFF.search(m.group(1))
        if km and title:
            h = int(km.group(1)) % 12 + (12 if km.group(3).lower() == "pm" else 0)
            d = time.gmtime()
            start = calendar.timegm(
                (d.tm_year, d.tm_mon, d.tm_mday, h, int(km.group(2)), 0, 0, 0, 0))
            start -= 3600  # kickoff times are Europe/London (BST in season)
            return [_prog(xid, start, start + 3 * 3600, title)]
        if title:
            return [_prog(xid, now, now + 12 * 3600, title)]
    return [_prog(xid, now, now + 24 * 3600, "No event scheduled",
                  "Event slot — fills in when the provider schedules one.")]


SYNTH_PREFIX = re.compile(r"^(PRIME|GO|TUBI|CITY|US|UK|DE)\s*:\s*", re.I)


def synth_programmes(xid, disp):
    """Looping 4h filler blocks so 24/7 channels don't show an empty guide."""
    title = SYNTH_PREFIX.sub("", disp) or disp
    base = int(time.time()) // 86400 * 86400
    return [_prog(xid, t, t + 4 * 3600, title,
                  "Plays around the clock — no schedule data published.")
            for t in range(base, base + 48 * 3600, 4 * 3600)]


def build_epg(base, user, pw, cfg, chosen):
    # ids with real provider EPG (no "mc" prefix) -> copy their programmes
    real_ids = {xid for _, _, xid, _, _, _, _ in chosen if not xid.startswith("mc")}
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        with fetch(f"{base}/xmltv.php?username={user}&password={pw}", timeout=600) as r:
            shutil.copyfileobj(r, tmp)
        raw = Path(tmp.name)

    kept_pr = 0
    covered = set()
    out_tmp = EPG_OUT.with_suffix(".xml.tmp")
    EPG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(out_tmp, "wb") as out:
        out.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        out.write(b'<tv generator-info-name="media-core-sync">\n')
        _epg_overrides = icon_overrides()
        # one <channel> per unique xmltv id, carrying logo + tuner-matching name
        emitted = set()
        for s, _group, xid, disp, _region, _chno, _mode in chosen:
            if xid in emitted:
                continue
            emitted.add(xid)
            icon = pick_logo(disp, s.get("stream_icon"), _epg_overrides)
            ch = f'<channel id="{xml_escape(xid)}"><display-name>{xml_escape(disp)}</display-name>'
            if icon:
                ch += f'<icon src="{xml_escape(icon)}" />'
            ch += "</channel>\n"
            out.write(ch.encode("utf-8"))
        # copy programmes for channels the provider has guide data for
        root = None
        for event, elem in ET.iterparse(raw, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag == "programme" and elem.get("channel") in real_ids:
                out.write(ET.tostring(elem, encoding="utf-8"))
                kept_pr += 1
                covered.add(elem.get("channel"))
            if elem.tag in ("channel", "programme"):
                elem.clear()
                if root is not None:
                    root.clear()
        raw.unlink()
        print(f"epg: {len(emitted)} channel entries (with logos), "
              f"{kept_pr} provider programmes for {len(covered)} channels")

        # channels with no provider programmes -> try external sources
        needy = {}
        for _s, _group, xid, disp, region, _chno, _mode in chosen:
            if xid not in covered and region:
                needy.setdefault(region, {}).setdefault(xid, disp)
        n_needy = sum(len(v) for v in needy.values())
        fed = merge_external_epg(out, cfg, needy) if n_needy else set()

        # event-slot channels: parse the current event out of the name
        ppv_state = {}
        ppv_n = 0
        for s, _g, xid, _d, _r, _c, mode in chosen:
            if mode != "ppv" or xid in covered or xid in fed or xid in ppv_state:
                continue
            for p in ppv_programmes(s.get("name") or "", xid):
                out.write(p)
                ppv_n += 1
            ppv_state[xid] = s["stream_id"]
        # whatever still has nothing: synthesize a looping guide from the name
        synth_ids = set()
        for _s, _g, xid, disp, _r, _c, _m in chosen:
            if (xid in covered or xid in fed or xid in ppv_state
                    or xid in synth_ids):
                continue
            for p in synth_programmes(xid, disp):
                out.write(p)
            synth_ids.add(xid)
        out.write(b"</tv>\n")
    out_tmp.replace(EPG_OUT)
    # the hourly ppv-refresh rewrites exactly these channels' programmes
    PPV_STATE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(PPV_STATE, json.dumps(ppv_state))
    total = len(covered | fed)
    print(f"epg: external sources covered {len(fed)} of {n_needy} uncovered "
          f"channels; total coverage {total}/{len(emitted)} unique guide ids")
    print(f"epg: ppv-parsed {len(ppv_state)} event slots ({ppv_n} programmes), "
          f"synthesized looping guide for {len(synth_ids)} channels")
    loki_alert.push("epg-sync",
                     f"level=info real={total} synth={len(synth_ids)} "
                     f"ppv={len(ppv_state)} total_channels={len(emitted)}")


HIGH_BITRATE = re.compile(r"⁴ᴷ|³⁸⁴⁰|4K|3840|ᴰᴼᴸᴮʸ|ᴰᵒˡᵇʸ|DOLBY", re.I)


def _build_vod_library(movies_dir, vods, id2name, prefixes, base, user, pw):
    """Dedupe + write one VOD library (general or one service). Same
    algorithm as the pre-2026-07-18 single-library build_vod, just scoped
    to whatever `vods` subset it's given."""
    movies_dir.mkdir(parents=True, exist_ok=True)
    # Dedupe priority: HD copies beat 4K/Dolby prints (the ~10 Mbit/s VPN
    # can't sustain high-bitrate remuxes), then EN-prefixed categories win
    # so the English copy of a title survives the dedupe.
    def priority(cid):
        n = id2name.get(cid, "")
        return (1 if HIGH_BITRATE.search(n) else 0,
                0 if n.startswith(prefixes) else 1)
    vods = sorted(vods, key=lambda v: priority(v.get("category_id")))
    titles = [clean_movie_title(v.get("name") or "") for v in vods]
    # base titles that exist with an explicit (Year): a year-less copy of
    # the same base is a duplicate print of that film, not a separate movie
    yeared = {m.group(1).lower()
              for t in titles if (m := re.match(r"^(.*) \(\d{4}\)$", t))}
    keep, dirs, written, skipped = set(), set(), 0, 0
    for v, title in zip(vods, titles):
        key = title.lower()
        if key in keep:  # duplicate (4K/HD copy, or non-EN when EN exists)
            skipped += 1
            continue
        if key in yeared and not re.search(r"\(\d{4}\)$", title):
            skipped += 1  # year-less duplicate of a (Year)-titled film
            continue
        ext = v.get("container_extension") or "mp4"
        url = f'{base}/movie/{user}/{pw}/{v["stream_id"]}.{ext}\n'
        strm = movies_dir / title / f"{title}.strm"
        try:
            if not strm.exists() or strm.read_text() != url:
                strm.parent.mkdir(exist_ok=True)
                strm.write_text(url)
                written += 1
        except OSError as e:
            print(f"WARNING: skipping {title!r}: {e}")
            continue
        keep.add(key)
        dirs.add(title)

    # prune guard: a flaky/partial provider response must never mass-delete
    # the library (Jellyfin item counts used to fluctuate because of this)
    existing = sum(1 for c in movies_dir.iterdir() if c.is_dir())
    pruned = 0
    if existing and len(dirs) < existing * PRUNE_GUARD:
        print(f"WARNING: {movies_dir.name}: provider returned {len(dirs)} "
              f"movies but {existing} exist on disk (< {PRUNE_GUARD:.0%}) — "
              f"SKIPPING prune")
    else:
        for child in movies_dir.iterdir():
            if child.is_dir() and child.name not in dirs:
                shutil.rmtree(child)
                pruned += 1
    return len(keep), written, skipped, pruned


def build_vod(base, user, pw, cfg):
    vcats = api(base, user, pw, "get_vod_categories")
    id2name = {c["category_id"]: c["category_name"] for c in vcats}
    prefixes = tuple(cfg["vod_category_prefixes"])
    excl = set(cfg.get("vod_exclude_categories") or [])
    if excl:
        # exclude mode: owner lists what to drop; every other category —
        # including ones the provider adds later — is included
        selected = {cid for cid, n in id2name.items() if n not in excl}
    else:
        exact = set(cfg["vod_categories"])
        selected = {
            cid for cid, n in id2name.items()
            if n in exact or (prefixes and n.startswith(prefixes))
        }
    vods = api(base, user, pw, "get_vod_streams")
    vods = [v for v in vods if v.get("category_id") in selected]

    # partition by target library: branded categories peel off into their
    # own service library, everything else stays in the general pool —
    # a title with copies in both a branded and a general category ends
    # up in both libraries (each independently deduped), reflecting where
    # it's actually available.
    buckets = {}
    for v in vods:
        svc = category_service(id2name.get(v.get("category_id"), ""))
        target = MOVIES_DIR if svc is None else MOVIES_SERVICE_DIRS[svc]
        buckets.setdefault(target, []).append(v)

    tot_keep = tot_written = tot_skipped = tot_pruned = 0
    parts = []
    for movies_dir, bucket in buckets.items():
        k, w, s, p = _build_vod_library(movies_dir, bucket, id2name, prefixes, base, user, pw)
        tot_keep += k; tot_written += w; tot_skipped += s; tot_pruned += p
        parts.append(f"{movies_dir.name}: {k}")
    print(f"vod: {tot_keep} movies across {len(buckets)} libraries "
          f"({tot_written} written/updated, {tot_skipped} duplicates, "
          f"{tot_pruned} pruned) — " + ", ".join(parts))
    loki_alert.push("epg-sync",
                     f"level=info event=vod_summary total_movies={tot_keep} "
                     f"libraries={len(buckets)} written={tot_written} "
                     f"duplicates={tot_skipped} pruned={tot_pruned}")


EP_TITLE = re.compile(r" - S(\d+)E(\d+)(?: - (.*))?$", re.I)


def category_prefix(names):
    """Detect a provider-wide "MRVL - "-style tag used by most of a
    category. Stripping only the majority token keeps legit dashed
    titles (e.g. "NCIS - Los Angeles") intact."""
    toks = {}
    for n in names:
        m = re.match(r"^([A-Z0-9]{2,6})\s+-\s+", n or "")
        if m:
            toks[m.group(1)] = toks.get(m.group(1), 0) + 1
    if not toks:
        return None
    tok = max(toks, key=toks.get)
    return tok if toks[tok] >= 0.6 * len(names) else None


def series_nfo(title, info):
    """Minimal tvshow.nfo from provider metadata: instant titles/plots/
    genres/posters without waiting on per-item TMDB lookups."""
    lines = ["<tvshow>", f"  <title>{xml_escape(title)}</title>"]
    if info.get("plot"):
        lines.append(f"  <plot>{xml_escape(info['plot'])}</plot>")
    for g in re.split(r"[/,]", info.get("genre") or ""):
        if g.strip():
            lines.append(f"  <genre>{xml_escape(g.strip())}</genre>")
    if info.get("releaseDate"):
        lines.append(f"  <premiered>{xml_escape(info['releaseDate'])}</premiered>")
    if info.get("rating"):
        lines.append(f"  <rating>{xml_escape(str(info['rating']))}</rating>")
    if info.get("cover"):
        lines.append(f'  <thumb aspect="poster">{xml_escape(info["cover"])}</thumb>')
    for actor in (info.get("cast") or "").split(",")[:12]:
        if actor.strip():
            lines.append(f"  <actor><name>{xml_escape(actor.strip())}</name></actor>")
    lines.append("</tvshow>")
    return "\n".join(lines) + "\n"


def write_if_changed(path, data):
    """Only touch files whose content differs (mtime churn = Jellyfin churn)."""
    try:
        if path.exists() and path.read_text() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return True


def _collect_chosen(cnames, name2id, base, user, pw):
    """Iterate categories in priority order, first title listed wins
    (within this bucket only) — mirrors build_vod's EN-first behaviour."""
    chosen = {}
    for cname in cnames:
        cid = name2id.get(cname)
        if cid is None:
            continue
        try:
            series = api(base, user, pw, f"get_series&category_id={cid}") or []
        except Exception as e:
            print(f"WARNING: get_series {cname!r} failed: {e}")
            continue
        pre = category_prefix([s.get("name") or "" for s in series])
        for s in series:
            raw = s.get("name") or ""
            if pre:
                raw = re.sub(r"^%s\s+-\s+" % re.escape(pre), "", raw)
            title = clean_movie_title(raw)
            if title and title.lower() not in chosen:
                chosen[title.lower()] = (title, s)
    return chosen


def _build_series_library(shows_dir, chosen, base, user, pw):
    """Fetch/cache series_info + write the show tree for one library
    (general or one service). SERIES_CACHE is shared across all libraries
    (keyed by series_id, not by target dir) so the same show never gets
    fetched twice even if it legitimately belongs in two buckets."""
    if not chosen:
        return 0, 0, 0, 0
    shows_dir.mkdir(parents=True, exist_ok=True)
    SERIES_CACHE.mkdir(parents=True, exist_ok=True)
    existing = {c.name for c in shows_dir.iterdir() if c.is_dir()}
    desired = set()
    fetched = failed = wrote = 0
    for title, s in chosen.values():
        sid = s.get("series_id")
        lm = str(s.get("last_modified") or "")
        cache_f = SERIES_CACHE / f"{sid}.json"
        info, cached = None, False
        if cache_f.exists():
            try:
                c = json.loads(cache_f.read_text())
                if c.get("last_modified") == lm:
                    info, cached = c["data"], True
            except Exception:
                pass
        if info is None:
            try:
                info = api(base, user, pw, f"get_series_info&series_id={sid}")
                fetched += 1
                atomic_write(cache_f, json.dumps(
                    {"last_modified": lm, "data": info}))
                time.sleep(0.5)   # be polite: ~6.5k series on first run;
                # 0.1s tripped the panel's anti-abuse (403s on API *and*
                # live streams for ~everything) on 2026-07-06 — keep ≥0.5
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"WARNING: series_info {sid} ({title}) failed: {e}")
                continue
        desired.add(title)
        show_dir = shows_dir / title
        if cached and show_dir.exists():
            continue  # unchanged upstream and present on disk: fast path
        keep_files = {"tvshow.nfo"}
        wrote += write_if_changed(
            show_dir / "tvshow.nfo", series_nfo(title, info.get("info") or {}))
        eps_map = info.get("episodes") or {}
        if isinstance(eps_map, list):
            # some panel titles return a list of season-lists, not a dict;
            # season 0 is real (specials), so only index-fallback on None
            new_map = {}
            for i, eps in enumerate(eps_map):
                s = (eps or [{}])[0].get("season")
                new_map[str(s if s is not None else i + 1)] = eps
            eps_map = new_map
        for snum, eps in eps_map.items():
            try:
                season = int(snum)
            except (TypeError, ValueError):
                continue
            sdir = f"Season {season:02d}"
            for ep in eps or []:
                try:
                    enum = int(ep.get("episode_num") or 0)
                except (TypeError, ValueError):
                    continue
                ext = ep.get("container_extension") or "mp4"
                stem = f"{title} S{season:02d}E{enum:02d}"
                rel = f"{sdir}/{stem}.strm"
                keep_files.add(rel)
                url = f'{base}/series/{user}/{pw}/{ep["id"]}.{ext}\n'
                wrote += write_if_changed(show_dir / rel, url)
                m = EP_TITLE.search(ep.get("title") or "")
                ep_title = clean_movie_title(m.group(3)) if m and m.group(3) else stem
                nfo = ("<episodedetails>\n"
                       f"  <title>{xml_escape(ep_title)}</title>\n"
                       f"  <season>{season}</season>\n"
                       f"  <episode>{enum}</episode>\n"
                       "</episodedetails>\n")
                rel_nfo = f"{sdir}/{stem}.nfo"
                keep_files.add(rel_nfo)
                wrote += write_if_changed(show_dir / rel_nfo, nfo)
        # per-show cleanup of episodes that vanished upstream. Restricted
        # to .strm/.nfo (found in review, 2026-08-17): keep_files only
        # ever tracks those two extensions, so the old blanket
        # "delete anything not in keep_files" also wiped Jellyfin's own
        # fetched artwork (poster/fanart/thumb) and any external .srt/.vtt
        # subtitles sitting in the same show folder, every time a show's
        # info genuinely changed upstream -- not permanently broken
        # (Jellyfin re-fetches on its next scan) but a real, needless
        # waste of bandwidth/TMDB calls and a temporary missing-artwork
        # flicker on every real update.
        for f in show_dir.rglob("*"):
            if f.is_file() and f.suffix in (".strm", ".nfo") and str(f.relative_to(show_dir)) not in keep_files:
                f.unlink()

    # prune guard: same rule as VOD — partial provider answers must never
    # mass-delete the library
    pruned = 0
    if existing and len(desired) < len(existing) * PRUNE_GUARD:
        print(f"WARNING: {shows_dir.name}: provider returned {len(desired)} "
              f"series but {len(existing)} exist on disk "
              f"(< {PRUNE_GUARD:.0%}) — SKIPPING prune")
    else:
        for name in existing - desired:
            shutil.rmtree(shows_dir / name)
            pruned += 1
    return len(desired), fetched, failed, wrote


def build_series(base, user, pw, cfg):
    want = cfg.get("series_categories") or []
    excl = cfg.get("series_exclude_categories")
    if not want and excl is None:
        return
    cats = api(base, user, pw, "get_series_categories")
    name2id = {c["category_name"]: c["category_id"] for c in cats}
    if excl is not None:
        # exclude mode: series_categories keeps its role as the dedupe
        # priority order; every non-excluded category the panel has (or
        # grows later) is appended after it, HD variants before 4K/Dolby
        excl = set(excl)
        rest = [c["category_name"] for c in cats
                if c["category_name"] not in excl
                and c["category_name"] not in set(want)]
        rest.sort(key=lambda n: bool(HIGH_BITRATE.search(n)))
        want = [n for n in want if n not in excl] + rest
    if not want:
        return
    for n in want:
        if n not in name2id:
            print(f"WARNING: series category not found: {n!r}")

    # partition category names (order preserved = dedupe priority) into
    # the general pool and each branded service, same split as build_vod
    general_want, service_want = [], {s: [] for s in SERVICE_SLUG}
    for cname in want:
        svc = category_service(cname)
        (service_want[svc] if svc else general_want).append(cname)

    buckets = [(SHOWS_DIR, general_want)]
    for svc, cnames in service_want.items():
        if cnames:
            buckets.append((SHOWS_SERVICE_DIRS[svc], cnames))

    tot_desired = tot_fetched = tot_failed = tot_wrote = 0
    parts = []
    any_chosen = False
    for shows_dir, cnames in buckets:
        chosen = _collect_chosen(cnames, name2id, base, user, pw)
        if chosen:
            any_chosen = True
        d, f, fa, w = _build_series_library(shows_dir, chosen, base, user, pw)
        tot_desired += d; tot_fetched += f; tot_failed += fa; tot_wrote += w
        parts.append(f"{shows_dir.name}: {d}")

    if not any_chosen:
        print("WARNING: provider returned no series — skipping series sync")
        return
    print(f"series: {tot_desired} shows across {len(buckets)} libraries "
          f"({tot_fetched} info fetched, {tot_failed} failed, "
          f"{tot_wrote} files written) — " + ", ".join(parts))
    loki_alert.push("epg-sync",
                     f"level=info event=series_summary total_shows={tot_desired} "
                     f"libraries={len(buckets)} fetched={tot_fetched} "
                     f"failed={tot_failed} written={tot_wrote}")


def jellyfin_refresh(names=("Refresh Guide", "Scan Media Library")):
    """Kick Jellyfin's guide refresh + library scan right after the sync."""
    if not JF_KEY_FILE.exists():
        print("jellyfin: no API key file, skipping refresh triggers")
        return
    tok = JF_KEY_FILE.read_text().strip()
    hdr = {"Authorization": f'MediaBrowser Token="{tok}"', "User-Agent": UA}
    try:
        req = urllib.request.Request(f"{JF_URL}/ScheduledTasks", headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as r:
            tasks = json.load(r)
        for name in names:
            t = next((t for t in tasks if t["Name"] == name), None)
            if not t:
                print(f"jellyfin: task {name!r} not found")
                continue
            if t["State"] != "Idle":
                print(f"jellyfin: {name} already {t['State']}, not retriggering")
                continue
            req = urllib.request.Request(
                f"{JF_URL}/ScheduledTasks/Running/{t['Id']}",
                headers=hdr, method="POST")
            urllib.request.urlopen(req, timeout=30).close()
            print(f"jellyfin: triggered {name}")
    except Exception as e:
        print(f"WARNING: jellyfin refresh triggers failed: {e}")


def main():
    t0 = time.time()
    try:
        env = read_env()
        base = env["XTREAM_BASE"].rstrip("/")
        user, pw = env["XTREAM_USER"], env["XTREAM_PASS"]
        cfg = json.loads(CONFIG_FILE.read_text())
        CFG_CACHE.update(cfg)      # gates modernise_name()
        chosen = build_playlist(base, user, pw, cfg)
        build_epg(base, user, pw, cfg, chosen)
        build_vod(base, user, pw, cfg)
        build_series(base, user, pw, cfg)
        jellyfin_refresh()
        print("sync complete")
        loki_alert.push("epg-sync",
                         f"level=info event=sync_complete "
                         f"duration_s={time.time() - t0:.1f}")
    except Exception as e:
        loki_alert.push("media-core-alerts",
                         f'level=alert source=xtream-sync msg="nightly sync failed: {e}"')
        raise


if __name__ == "__main__":
    sys.exit(main())
