#!/usr/bin/env python3
"""Search the provider's FULL live catalogue for a Milwaukee MY24 / WVTV feed
and anything Bucks-related. Read-only; prints names only, never credentials."""
import importlib.util, json, re, sys, urllib.request
sys.path.insert(0, "/srv/media-core/sync")
spec = importlib.util.spec_from_file_location("xs", "/srv/media-core/sync/xtream-sync.py")
xs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xs)

env = {}
for line in open("/srv/media-core/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

base = env["XTREAM_BASE"].rstrip("/")
streams = xs.api(base, env["XTREAM_USER"], env["XTREAM_PASS"], "get_live_streams")
print("provider carries", len(streams), "live streams")

PAT = re.compile(r"\bMY ?-?24\b|WVTV|WCGV|MYNETWORK|MY NETWORK|BUCKS", re.I)
print("\n--- MY24 / WVTV / Bucks matches ---")
hits = [s for s in streams if PAT.search(s.get("name", ""))]
for s in hits:
    print("  %-8s %s" % (s.get("stream_id"), s.get("name")))
print(len(hits), "matches")

MIL = re.compile(r"MILWAUKEE|WISN|WTMJ|WITI|WDJT|WMLW|WYTU|WVCY", re.I)
print("\n--- everything Milwaukee ---")
for s in streams:
    if MIL.search(s.get("name", "")):
        print("  %-8s %s" % (s.get("stream_id"), s.get("name")))

json.dump([{"id": s.get("stream_id"), "name": s.get("name"),
            "cat": s.get("category_id")} for s in streams],
          open("/root/provider-catalogue.json", "w"), indent=1)
print("\nfull catalogue -> /root/provider-catalogue.json")
