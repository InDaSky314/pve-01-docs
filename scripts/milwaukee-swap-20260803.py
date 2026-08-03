#!/usr/bin/env python3
"""Swap the two dead Bally Sports Wisconsin feeds for working Bucks/Brewers feeds.

120/121 currently carry Bally Sports Wisconsin, both confirmed dead: the FanDuel
Sports Network RSNs ceased operations April 2026 and these return a
byte-identical black frame. The provider carries no MY24/WVTV, but does carry
league-run team feeds for both Milwaukee clubs, both verified playing tonight.

Dict form is used deliberately: its value IS the display name, so these two
arrive already in the agreed Title Case rather than needing a later rename —
and a rename is the one operation in this estate that deletes and recreates a
channel.
"""
import json, shutil, sys

CFG = "/srv/media-core/sync/config.json"
OLD = {"430367", "430368"}
NEW = {"633369": "Milwaukee Bucks HD",       # NBA League Pass team feed
       "1904210": "Milwaukee Brewers HD"}    # MLB.TV team feed

cfg = json.load(open(CFG))
target = None
for s in cfg["live_selections"]:
    if s.get("group") == "Sports Front Page":
        target = s
        break
if target is None:
    sys.exit("Sports Front Page group not found")

ids = target["ids"]
assert isinstance(ids, dict), "expected dict form, got %s" % type(ids).__name__
print("before:", len(ids), "ids")

removed = [(k, ids[k]) for k in list(ids) if k in OLD]
for k, _ in removed:
    del ids[k]
for k, v in NEW.items():
    if k in ids:
        sys.exit("id %s already present — aborting rather than duplicating" % k)

# Numbers are assigned by walking the group in order from start_chno, so
# putting the new pair first is what puts them on 120/121.
final = dict(NEW)
final.update(ids)
target["ids"] = final

print("removed:")
for k, v in removed:
    print("   ", k, v)
print("added:")
for k, v in NEW.items():
    print("   ", k, v)
print("after:", len(final), "ids")

if "--apply" not in sys.argv:
    print("\n(dry run — pass --apply)")
    sys.exit(0)

json.dump(cfg, open(CFG, "w"), indent=2)
json.load(open(CFG))          # re-parse: a broken config breaks the whole sync
print("\nwrote", CFG, "and it re-parses")
