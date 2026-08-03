#!/usr/bin/env python3
"""Build the channel rename map: drop group prefixes, Title Case, fix stale brands.

Produces a reviewable JSON map. Applies nothing — the rename itself is a
separate, guarded step, because a rename in this estate deletes and recreates
the channel in both Jellyfins and re-keys its artwork file.

Rules, in order:
  1. Group/country prefixes are dropped (US:, GO:, PRIME:, DE:, UK:, ...).
     Market prefixes are kept — "Green Bay:" identifies the feed, "US:" does not.
  2. Stale brands are corrected from the verified map below.
  3. Title Case. NOTE: the provider's names are entirely uppercase, so
     capitalisation carries no information — an acronym cannot be detected by
     looking at it. Only the explicit list below is left uppercase, plus
     parenthesised call signs, which are unambiguous by position.
  4. Quality suffix (HD / 4K / FHD ...) is preserved.
  5. Collisions are resolved explicitly, never silently.
"""
import json
import re
import sqlite3
import sys
from collections import Counter

NP = "/srv/jellyfin-npvr/nextpvr/config/npvr.db3"

DROP_PREFIX = re.compile(
    r"^(US|UK|DE|GO|PRIME|TUBI|MLB|NBA|NHL|NFL|CA|IT|FR|ES|NL|PT|PL|TR|AR|BR|MX)"
    r"\s*:\s*", re.I)
KEEP_PREFIX = re.compile(
    r"^(Madison|Green Bay|Milwaukee|Chicago|Denver|New York|Los Angeles|"
    r"Boston|Philadelphia|Seattle|Atlanta|Dallas|Houston|Miami|Phoenix):", re.I)

# Only genuine initialisms. A word is NOT added here just because the source
# shouts it — the source shouts everything.
ACRONYMS = {
    "ABC", "ACC", "AMC", "AMHQ", "AWE", "AXS", "BBC", "BET", "BTN", "CBC",
    "CBS", "CHSN", "CMT", "CNBC", "CNN", "CSN", "CW", "DIY", "DW", "ESPN",
    "ESPN2", "ESPNU", "EWTN", "FX", "FXM", "FXX", "FYI", "GSN", "HBO", "HGTV",
    "HLN", "IFC", "INSP", "ION", "KBS", "MAVTV", "MLB", "MSG", "MSNBC", "MTV",
    "NASA", "NBA", "NBC", "NDR", "NESN", "NFL", "NHL", "NPO", "OAN", "OWN",
    "PBS", "PPV", "QVC", "RAI", "RT", "RTL", "SEC", "SNY", "SWR", "TBN",
    "TBS", "TCM", "TLC", "TNT", "TRT", "TV", "TVG", "UEFA", "UFC", "USA",
    "VH1", "VOD", "WDR", "WGN", "WWE", "ZDF", "AFV", "BR", "MDR", "ARD",
    "HD", "FHD", "UHD", "SD", "4K", "8K", "II", "III", "IV", "DC", "LA", "NY",
    "US", "UK", "XL", "SP", "PBS", "CNN", "HBO",
}
LOWER_WORDS = {"of", "the", "and", "a", "an", "in", "on", "for", "to", "at",
               "by", "with", "vs", "de", "la", "el", "y"}
# A call sign can only be recognised by POSITION, not by casing: the source
# is entirely uppercase, so "WILD" and "WEST" match any letter pattern a
# call sign would. Parentheses are the reliable signal.
CALLSIGN = re.compile(r"^[KW][A-Z]{2,3}(?:-[A-Z]{2})?$")

SPECIAL = {
    "ME TV": "MeTV", "METV": "MeTV",
    "METV PLUS (WZME) HD (SP)": "MeTV Plus WZME HD",
    "MY 9 WWOR NEW YORK": "My 9 WWOR New York",
    "MYTV 9 (WCTX) WATERBURY HD": "MyTV 9 WCTX Waterbury HD",
    "TRUTV WEST 4K": "truTV West 4K",
    "ESPNEWS": "ESPNews",
    "A&E HD": "A&E HD",
    "FETV": "FETV", "DABL": "Dabl", "BUZZR": "Buzzr",
    "NFL REDZONE": "NFL RedZone",
    "BEIN SPORTS": "beIN Sports",
    "AXS TV NOW": "AXS TV Now",
    "AMC PLUS": "AMC+",
    "DISCOVERY+ 4K": "Discovery+ 4K",
}

# Verified 2026-08-03, each confirmed on screen from the stream itself or from
# a dated source. See docs/handoff-20260804.md.
BRAND_FIX = {
    "CSN BAY AREA HD": "NBC Sports Bay Area HD",
    "CSN BOSTON HD": "NBC Sports Boston HD",
    "CSN CALIFORNIA HD": "NBC Sports California HD",
    "CSN PHILADELPHIA HD": "NBC Sports Philadelphia HD",
    "CSN PHILADELPHIA PLUS HD": "NBC Sports Philadelphia Plus HD",
    "CSN CHICAGO HD": "Chicago Sports Network HD",
    "CSN CHICAGO PLUS HD": "Chicago Sports Network Plus HD",
    "CSN WASHINGTON HD": "Monumental Sports Network HD",
    "BALLY SPORTS SOCAL HD": "FanDuel Sports Network SoCal HD",
    "FANDUEL MIDWEST": "FanDuel TV Midwest",
}


def title_case(s):
    words = s.split()
    out = []
    for i, w in enumerate(words):
        lead = re.match(r"^[\(\[]*", w).group(0)
        tail = re.search(r"[\)\]]*$", w).group(0)
        core = w[len(lead):len(w) - len(tail)] if tail else w[len(lead):]
        if not core:
            out.append(w)
            continue
        parenthesised = lead.startswith("(") and tail.endswith(")")
        if core.upper() in ACRONYMS:
            new = core.upper()
        elif parenthesised and CALLSIGN.match(core.upper()):
            new = core.upper()
        elif re.fullmatch(r"[\d]+[A-Za-z]?", core):
            new = core.upper()
        elif core.lower() in LOWER_WORDS and i > 0:
            new = core.lower()
        else:
            # keep internal punctuation: "MOVIES/MYSTERIES" -> "Movies/Mysteries"
            new = re.sub(r"[A-Za-z]+",
                         lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
                         core)
        out.append(lead + new + tail)
    return " ".join(out)


def new_name(old):
    market = ""
    m = KEEP_PREFIX.match(old)
    if m:
        market = old[:m.end()] + " "
        rest = old[m.end():].strip()
    else:
        rest = DROP_PREFIX.sub("", old).strip()
    rest = DROP_PREFIX.sub("", rest).strip()
    rest = rest.lstrip(": ").strip()

    key = rest.upper()
    if key in BRAND_FIX:
        rest = BRAND_FIX[key]
    elif key in SPECIAL:
        rest = SPECIAL[key]
    else:
        rest = title_case(rest)
    return (market + rest).strip()


def main():
    c = sqlite3.connect("file:%s?mode=ro" % NP, uri=True)
    rows = list(c.execute(
        "select oid, number, name from CHANNEL order by cast(number as integer)"))

    mapping = [{"oid": o, "number": n, "old": old, "new": new_name(old)}
               for o, n, old in rows]
    for m in mapping:
        m["changed"] = m["new"] != m["old"]

    counts = Counter(m["new"] for m in mapping)
    collisions = {k: v for k, v in counts.items() if v > 1}
    for m in mapping:
        m["collides"] = m["new"] in collisions

    changed = [m for m in mapping if m["changed"]]
    print("channels      :", len(mapping))
    print("names changed :", len(changed))
    print("COLLISIONS    :", len(collisions))
    for k, v in collisions.items():
        print("   %dx %r" % (v, k))
        for m in mapping:
            if m["new"] == k:
                print("        <- %-8s %s" % (m["number"], m["old"]))
    json.dump(mapping, open("/root/rename-map.json", "w"), indent=1)
    print("\nwrote /root/rename-map.json")

    if "--sample" in sys.argv:
        lo, hi = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 10**9)
        print("\n--- changes %s-%s ---" % (lo, hi))
        for m in changed:
            try:
                num = int(m["number"])
            except (TypeError, ValueError):
                continue
            if lo <= num <= hi:
                print("  %-6s %-46s -> %s" % (m["number"], m["old"], m["new"]))


if __name__ == "__main__":
    main()
