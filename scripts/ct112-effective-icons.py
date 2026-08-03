"""Export CT 112's *effective* channel artwork — the exact bytes NextPVR
serves for each live channel, which have been verified byte-identical to what
Jellyfin renders. Keyed by the production display name with : / | stripped,
which is what xtream-sync's pick_logo() looks up.

Run inside CT 112. Writes to /root/ct112-effective-icons/.
"""
import os, re, shutil, sqlite3, json

ROOT = "/srv/jellyfin-npvr"
ICO = ROOT + "/nextpvr/config/media/channels"
NP = ROOT + "/nextpvr/config/npvr.db3"
OUT = "/root/ct112-effective-icons"

PROD_STRIP = re.compile(r"[:/|]")        # what xtream-sync strips
FILE_STRIP = re.compile(r'[:/\\|*?"<>]')  # what NextPVR strips

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

np = sqlite3.connect("file:%s?mode=ro" % NP, uri=True)
names = [r[0] for r in np.execute("select name from CHANNEL")]

written, missing = [], []
for name in names:
    base = FILE_STRIP.sub("", name)
    src = None
    for ext in (".jpg", ".png"):          # NextPVR's own precedence
        p = os.path.join(ICO, base + ext)
        if os.path.exists(p):
            src = p
            break
    if src is None:
        missing.append(name)
        continue
    key = PROD_STRIP.sub("", name).strip()
    dst = os.path.join(OUT, key + os.path.splitext(src)[1])
    shutil.copy2(src, dst)
    written.append((name, key, os.path.basename(dst)))

print("channels:", len(names), " written:", len(written), " missing:", len(missing))
for m in missing:
    print("  MISSING", m)
json.dump([{"channel": c, "key": k, "file": f} for c, k, f in written],
          open("/root/ct112-effective-icons.json", "w"), indent=1)
