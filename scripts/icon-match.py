#!/usr/bin/env python3
"""Match CT 112's generic-icon channels against the tv-logos repository.

Written after two agy attempts produced nothing -- both died mid-task while
emitting a long matching script ("stream cut out"), despite exiting 0. The job
is small and mechanical, so it is done here directly.

Method is the one that works on this project: pull the repository file index
ONCE via the GitHub trees API and match offline. The alternative -- guessing
per-channel URLs and fetching to see what 404s -- was tried previously at ~8
requests per channel, almost all misses, and was far too slow to finish.

Output: /root/agy-reports/icon-research.json, one row per candidate, with
logo_url null where nothing matched. A null is a correct answer; a stretched
match is worse than a placeholder because it looks deliberate rather than
missing.
"""
import json
import re
import subprocess
import urllib.request
import collections

TREE_API = ("https://api.github.com/repos/tv-logo/tv-logos/git/trees/"
            "main?recursive=1")
RAW_BASE = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/"
OUT = "/root/agy-reports/icon-research.json"

# Suffixes that carry no identity and only hurt matching.
NOISE = re.compile(
    r"\b(HD|FHD|UHD|4K|SD|HEVC|H265|H264|RAW|BACKUP|ALT|VIP|PLUS|SAT|"
    r"1080P?|720P?|2160P?|3840P?)\b", re.I)
NONWORD = re.compile(r"[^a-z0-9]+")
# Leading country tag the provider prepends: "US ", "IT| ", "UK: "
COUNTRY_PREFIX = re.compile(r"^(US|UK|CA|IT|DE|FR|ES|NL|PT|PL|TR|AR|BR|MX)\b[|:\s-]*", re.I)
# US call sign, e.g. WMSN / KTLA
CALLSIGN = re.compile(r"\b([KW][A-Z]{2,3})\b")


def norm(s: str) -> str:
    s = COUNTRY_PREFIX.sub("", s)
    s = NOISE.sub(" ", s)
    s = NONWORD.sub(" ", s.lower()).strip()
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"^the ", "", s)


def variants(s: str) -> set:
    """Normalised forms to try.

    Two gaps cost real matches on the first pass: a leading "the"
    ("BOB ROSS CHANNEL" vs "the-bob-ross-channel") and inconsistent spacing
    ("FAIL ARMY" vs "failarmy", "BOUNCETV" vs "bounce-tv"). Both sides get
    spaced and de-spaced forms so either spelling lands.
    """
    n = norm(s)
    return {n, n.replace(" ", "")}


def fetch_tree():
    req = urllib.request.Request(TREE_API, headers={
        "User-Agent": "media-core-icon-audit",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    paths = [n["path"] for n in data.get("tree", [])
             if n["type"] == "blob" and n["path"].lower().endswith(".png")]
    return paths, data.get("truncated", False)


def main():
    raw = subprocess.run(["pct", "exec", "112", "--", "cat",
                          "/root/icon-candidates.json"],
                         capture_output=True, text=True, check=True).stdout
    candidates = json.loads(raw)["candidates"]
    print(f"candidates: {len(candidates)}")

    paths, truncated = fetch_tree()
    print(f"tv-logos png paths: {len(paths)}  truncated={truncated}")
    if truncated:
        print("WARNING: tree truncated; matches will be incomplete")

    # index: normalised stem -> list of paths (US first, it is a US lineup)
    index = collections.defaultdict(list)
    for p in paths:
        stem = p.rsplit("/", 1)[-1][:-4]
        # strip the trailing country code the repo appends: -us, -uk, -de
        stem_nc = re.sub(r"-[a-z]{2}$", "", stem)
        for v in variants(stem_nc):
            index[v].append(p)
    for k in index:
        index[k].sort(key=lambda p: (not p.startswith("countries/united-states"), len(p)))

    rows, counts = [], collections.Counter()
    for ch in candidates:
        n = norm(ch)
        url = conf = None
        note = ""
        src = "tv-logos"

        hit_exact = next((index[v] for v in variants(ch) if v in index), None)
        if hit_exact:
            url, conf = RAW_BASE + hit_exact[0], "high"
        else:
            # call sign is the strongest signal for US locals
            cs = CALLSIGN.search(ch)
            hit = None
            if cs:
                token = cs.group(1).lower()
                for k, v in index.items():
                    if token in k.split():
                        hit = v[0]
                        break
                if hit:
                    url, conf = RAW_BASE + hit, "high"
                    note = f"matched on call sign {cs.group(1)}"
            if not url:
                # Substring matching, but only when the overlap dominates both
                # sides. Loose substrings produced confident nonsense on the
                # first pass: "PRIME MSG SPORTSZONE", "PRIME SPORTSGRID" and
                # "PRIME PICKLEBALLTV" all matched a Hungarian "prime" logo,
                # and "MDR HD SACHSEN" matched mdr-sachsen-anhalt, a different
                # Land. Require the shorter string to cover most of the longer
                # one, and the first token to agree.
                best = None
                for k, v in index.items():
                    if len(k) <= 4 or not (k in n or n in k):
                        continue
                    if k.split()[0] != n.split()[0]:
                        continue
                    ratio = min(len(k), len(n)) / max(len(k), len(n))
                    if ratio < 0.65:
                        continue
                    if best is None or ratio > best[0]:
                        best = (ratio, k, v)
                if best:
                    ratio, k, v = best
                    url, conf = RAW_BASE + v[0], "medium"
                    note = f"substring match on '{k}' (overlap {ratio:.2f})"

        if not url:
            conf = "low"
            note = "no match in tv-logos index"

        if re.search(r"[:/|]", ch):
            note = (note + "; " if note else "") + \
                   "name contains : / or | -- filename strips it at install time"

        counts[conf] += 1
        rows.append({"channel": ch, "logo_url": url, "source": src if url else None,
                     "confidence": conf, "notes": note})

    with open(OUT, "w") as fh:
        json.dump(rows, fh, indent=1)

    print(f"\nwrote {OUT}")
    print(f"  high   : {counts['high']}")
    print(f"  medium : {counts['medium']}")
    print(f"  low    : {counts['low']}  (no match)")


if __name__ == "__main__":
    main()
