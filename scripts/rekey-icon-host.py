#!/usr/bin/env python3
"""Re-key the icon host after a naming change, bridging via the provider catalogue.

The icon host is keyed by display name with : / | stripped. When the display
name changes, the file is orphaned under its old key.

Mapping the old key to the new one cannot be done from the filenames alone:
the colon is already stripped, so "DE ARD HD" no longer looks prefixed and the
naming rules leave it as-is. The provider catalogue still holds the original
"DE: ARD HD", which is the bridge.
"""
import json, os, re, shutil, sys
ICONS = "/root/icon-archive/extracted"
CAT = "/root/provider-catalogue.json"
PLAYLIST = "/root/playlist.m3u"
sys.path.insert(0, "/root")
import channel_naming as cn

STRIP = re.compile(r"[:/|]")
DECOR = re.compile(r"[ᲀ-᷿⁰-₟ʰ-˿]")


def clean(n):
    n = DECOR.sub("", n or "").replace(",", " ")
    for ch in ("'", "’", "`"):
        n = n.replace(ch, "")
    return re.sub(r"\s+", " ", n).strip(" -")


def key(n):
    return STRIP.sub("", clean(n)).strip()


have = {os.path.splitext(f)[0] for f in os.listdir(ICONS)}
names = [m.group(1) for line in open(PLAYLIST)
         for m in [re.search(r'tvg-name="([^"]+)"', line)] if m]

# new display name -> old icon key, via the provider's original name
bridge = {}
for c in json.load(open(CAT)):
    raw = c.get("name")
    if not raw:
        continue
    bridge.setdefault(key(cn.modernise(clean(raw))), key(raw))

missing = [n for n in names if key(n) not in have]
plan, orphan = [], []
for n in missing:
    src = bridge.get(key(n))
    (plan if (src and src in have) else orphan).append((src, n))

print("published      :", len(names))
print("already keyed  :", len(names) - len(missing))
print("re-keyable     :", len(plan))
print("no source      :", len(orphan))
for s, n in orphan[:10]:
    print("     no icon for", n)
if "--apply" in sys.argv:
    for s, n in plan:
        shutil.copy2(os.path.join(ICONS, s + ".png"),
                     os.path.join(ICONS, key(n) + ".png"))
    print("copied", len(plan))
