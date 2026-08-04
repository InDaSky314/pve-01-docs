#!/usr/bin/env python3
"""Apply the lineup modernisation to config.json: remove dead channels, rename the rest.

One pass, because a rename is the expensive operation here — it deletes and
recreates the channel in both Jellyfins and re-keys its artwork file — and
doing removals and renames separately would pay that cost twice.

  * removals come from removal-candidates.json ("remove" only: dead air in two
    independent passes AND a citation). The 28 "needs_research" entries are
    left alone.
  * names come from the same rules as build-rename-map.py: group prefixes
    dropped, market prefixes kept, Title Case with an explicit acronym list,
    quality suffix preserved, verified brand fixes applied.
  * every group is written in dict form, because the dict value IS the display
    name — that is the only way config.json can express a rename.

Dry run by default.
"""
import json
import re
import sys
from collections import Counter

CFG = "/srv/media-core/sync/config.json"
CAT = "/root/provider-catalogue.json"
REMOVALS = "/root/removal-candidates.json"
sys.path.insert(0, "/root")
sys.path.insert(0, "/srv/media-core/sync")
from rename_map import new_name          # noqa: E402  same rules, one source

import importlib.util                    # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "xs", "/srv/media-core/sync/xtream-sync.py")
_xs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_xs)
clean_channel_name = _xs.clean_channel_name   # strips the ᴴᴰ/ᴿᴬᵂ decorations

# Event-pool groups are NOT renamed. Their provider names are fixture-derived
# ("NBA Summer League Lakers Vs. Bulls Jul 16 :Nba 03") and ppv-refresh
# rewrites the ids hourly, so any name we wrote would be stale within the hour
# and collides with its sibling slots. They also carry no country prefix, so
# there is nothing to gain.
# Fixture slots are not renamed: ppv-refresh rewrites their ids hourly and the
# provider name is derived from the match ("NBA Summer League Lakers Vs. Bulls
# Jul 16 :Nba 03"), so any name written here is stale within the hour and
# collides with its sibling slots.
#
# This is a per-CHANNEL test, not per-group. The pool groups also hold stable
# channels — "US: NHL NETWORK", "DE: SKY SPORT BUNDESLIGA 1" — and excluding
# whole groups left 152 of those still carrying a country prefix.
# Discriminator: inside the pool groups, a FIXTURE slot never carries a
# country prefix ("NBA Summer League Lakers Vs. Bulls Jul 16 :Nba 03") while a
# stable channel always does ("US: NHL NETWORK", "DE: SKY SPORT BUNDESLIGA 1").
# Matching on the prefix is exact where a fixture-shape regex was not.
POOL_GROUPS = {"NFL", "MLB", "NBA", "NHL", "UEFA", "UK Football", "Soccer PPV",
               "Bundesliga", "BBC Streams", "Big Brother", "HBO Max"}
COUNTRY_PREFIXED = re.compile(
    r"^(US|UK|DE|GO|PRIME|TUBI|MLB|NBA|NHL|NFL)\s*:", re.I)

# Two channels legitimately resolve to the same name because the provider
# carries the same network from two sources. Disambiguated by source, which is
# what a viewer would actually want to know. Everything else that collides is
# a bug, and the script refuses rather than guessing.
COLLISION_FIX = {
    "1568713": "MLB Network (DirecTV)",   # GO: MLB NETWORK
    "1568720": "MeTV (DirecTV)",          # GO: METV
    "616097":  "NBA TV HD (Alt)",         # duplicate of 607602, both "NBA: NBA TV HD"
}


# The catalogue keeps the provider's quality markers (ᴴᴰ, ᴿᴬᵂ, ᴱˣ) which
# xtream-sync strips when it builds the display name. Match on a normalised
# key or the removal list silently misses entries -- it missed two FanDuel
# feeds that way.
MARKERS = re.compile(r"[\u1d2c-\u1d6a\u2070-\u209f\u00b2\u00b3\u00b9]+")


def norm(n):
    return MARKERS.sub("", n or "").strip().rstrip("|").strip()


def main():
    apply = "--apply" in sys.argv
    cfg = json.load(open(CFG))
    catalogue = {str(c["id"]): c["name"] for c in json.load(open(CAT))}
    dead = {norm(r["channel"]) for r in json.load(open(REMOVALS))["remove"]}

    removed, renamed, kept = [], [], 0
    for sel in cfg["live_selections"]:
        ids = sel.get("ids")
        if ids is None:
            continue                      # category-driven group, no id list
        if sel.get("group") in POOL_GROUPS:
            # Left as a plain id list. Converting to dict form would force a
            # display name onto every fixture slot, and the catalogue carries
            # the same fixture name twice, so they collide. Removals still
            # apply; the residual country prefixes here are Bundesliga and two
            # NHL entries, recorded in the audit doc.
            if isinstance(ids, list):
                keep_ids = [i for i in ids
                            if norm(catalogue.get(str(i))) not in dead]
                removed.extend((str(i), catalogue.get(str(i)))
                               for i in ids if norm(catalogue.get(str(i))) in dead)
                sel["ids"] = keep_ids
            continue
        cur = ids if isinstance(ids, dict) else {str(i): None for i in ids}
        out = {}
        for sid, disp in cur.items():
            display = disp or catalogue.get(str(sid))
            if display is None:
                if disp is not None:
                    out[sid] = disp       # dict form: keep the name as found
                    kept += 1
                    continue
                # list form and the id is not in the catalogue: drop it rather
                # than emit a null name, which sync cannot handle
                removed.append((sid, "(unknown id, not in catalogue)"))
                continue
            if norm(display) in dead:
                removed.append((sid, display))
                continue
            nn = COLLISION_FIX.get(str(sid)) or new_name(clean_channel_name(display))
            if nn != display:
                renamed.append((sid, display, nn))
            out[sid] = nn
        assert all(v for v in out.values()), \
            "refusing to write a null display name in group %r" % sel.get("group")
        sel["ids"] = out

    names = [v for s in cfg["live_selections"]
             if isinstance(s.get("ids"), dict) for v in s["ids"].values() if v]
    collisions = {k: v for k, v in Counter(names).items() if v > 1}

    print("removed :", len(removed))
    print("renamed :", len(renamed))
    print("unknown ids left as-is:", kept)
    print("COLLISIONS:", len(collisions))
    for k, v in collisions.items():
        print("   %dx %s" % (v, k))
    print("\nsample removals:")
    for r in removed[:5]:
        print("   ", r[1])
    print("sample renames:")
    for r in renamed[:8]:
        print("   %-46s -> %s" % (r[1][:46], r[2]))

    if collisions:
        print("\nREFUSING: duplicate display names would make two channels "
              "indistinguishable to Threadfin, which keys on tvg-name.")
        return 1
    if not apply:
        print("\n(dry run — pass --apply)")
        return 0

    json.dump(cfg, open(CFG, "w"), indent=2)
    json.load(open(CFG))
    print("\nwrote", CFG, "and it re-parses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
