"""Channel display-name rules for Media-Core. One source of truth.

Imported by xtream-sync, which applies it to every provider-derived name, and
by scripts/build-rename-map.py, which previews a change before making it.
Keeping the rules in one module is the whole point: while they lived in two
places the two drifted, and 152 channels kept a country prefix that everything
else had lost.

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
import re


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
    "US", "UK", "XL", "SP", "PBS", "CNN", "HBO", "DP", "F1", "PGA",
    # German / European initialisms — the DE block is 108 channels
    "ZDF", "ZDFNEO", "ZDFINFO", "3SAT", "ARTE", "ARD", "SRF", "ORF", "RBB",
    "HR", "MDR", "NDR", "WDR", "SWR", "BR", "KIKA", "DMAX", "SIXX", "VOX",
    "RTL", "N-TV", "NTV", "SAT.1", "PRO7", "TLC", "DAZN", "HGTV",
    "ATP", "WTA", "MMA", "WWE", "AEW", "NASCAR", "IMSA",
}
# Brands with internal capitals. Title Case cannot derive these.
BRANDWORDS = {"fanduel": "FanDuel", "metv": "MeTV", "bein": "beIN",
              "trutv": "truTV", "mytv": "MyTV", "espnews": "ESPNews",
              "directv": "DirecTV", "youtube": "YouTube", "iheartradio":
              "iHeartRadio", "mlbtv": "MLB.TV", "nfl.com": "NFL.com"}
LOWER_WORDS = {"of", "the", "and", "a", "an", "in", "on", "for", "to", "at",
               "by", "with", "vs", "de", "la", "el", "y"}
# A call sign can only be recognised by POSITION, not by casing: the source
# is entirely uppercase, so "WILD" and "WEST" match any letter pattern a
# call sign would. Parentheses are the reliable signal.
CALLSIGN = re.compile(r"^[KW][A-Z]{2,3}(?:-[A-Z]{2})?$")

# Checked against the FULL provider name, before the group tag is stripped.
# Needed where the tag is the only thing distinguishing two channels: dropping
# "DE:" from "DE: MTV HD" collides it with the US feed, and Threadfin keys on
# tvg-name, so the two would become one.
FULL_NAME_OVERRIDES = {
    "DE: MTV HD": "MTV HD (Germany)",
    "DE: MTV LIVE HD": "MTV Live HD (Germany)",
    "DE: COMEDY CENTRAL HD": "Comedy Central HD (Germany)",
    "DE: DISCOVERY CHANNEL HD": "Discovery Channel HD (Germany)",
}
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
    # The provider ships this one mangled ("DE.tsCHLAND"); no transform can
    # recover it, so it is stated outright.
    "SERVUS TV DE.TSCHLAND HD": "Servus TV Deutschland HD",
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
        elif core.lower() in BRANDWORDS:
            new = BRANDWORDS[core.lower()]
        elif core.lower() in LOWER_WORDS and i > 0:
            new = core.lower()
        else:
            # keep internal punctuation: "MOVIES/MYSTERIES" -> "Movies/Mysteries"
            # Capitalise word-initial letters only. A naive [A-Za-z]+ pass
            # turns "WOMEN'S" into "Women'S" and "AC/DC" into "Ac/Dc".
            # [^\W\d_] is "any Unicode letter". [A-Za-z] here turned
            # "KÖLN" into "KÖLn": the umlaut is not an ASCII letter, so the
            # lookbehind treated "LN" as the start of a fresh word.
            new = re.sub(r"(?<![^\W\d_])([^\W\d_])([^\W\d_]*)",
                         lambda m: m.group(1).upper() + m.group(2).lower(),
                         core, flags=re.UNICODE)
        out.append(lead + new + tail)
    return " ".join(out)


def modernise(old):
    if not old:
        return old
    if old in FULL_NAME_OVERRIDES:
        return FULL_NAME_OVERRIDES[old]
    # Real bug found + verified 2026-08-17: KEEP_PREFIX used to get
    # checked against `old` BEFORE stripping DROP_PREFIX, so an input
    # like "US: Milwaukee: WTMJ HD" never matched KEEP_PREFIX at all
    # (the string starts with "US:", not a city name) and silently lost
    # its market tag entirely -- confirmed directly: old logic produced
    # market="" for that exact input, rest="Milwaukee: WTMJ HD" (the
    # city name stuck inside rest instead of being extracted). Stripping
    # DROP_PREFIX first fixes it: market="Milwaukee: ", rest="WTMJ HD".
    market = ""
    rest = DROP_PREFIX.sub("", old).strip()
    m = KEEP_PREFIX.match(rest)
    if m:
        market = rest[:m.end()] + " "
        rest = rest[m.end():].strip()
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




# build-rename-map.py imported this under the old name.
new_name = modernise
