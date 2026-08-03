"""End-user-layer verification for CT 112 channel artwork.

For every channel Jellyfin exposes over its HTTP API, fetch the image the
client actually renders and compare it byte-for-byte against what NextPVR
serves for that channel. Nothing here reads a database row.
"""
import json, hashlib, sqlite3, urllib.request, collections, sys

JF = "http://192.168.9.219:8096"
KEY = "1f74eabb57a5a6165e67c08aed0108b6"
NPVR = "http://localhost:8866"


def get(url, hdr=None, timeout=30):
    req = urllib.request.Request(url, headers=hdr or {})
    return urllib.request.urlopen(req, timeout=timeout).read()


auth = {"Authorization": "MediaBrowser Token=" + KEY}
data = json.loads(get(JF + "/LiveTv/Channels?limit=2000", auth))
items = data["Items"]
print("jellyfin channels:", len(items))

np = sqlite3.connect(
    "file:/srv/jellyfin-npvr/nextpvr/config/npvr.db3?mode=ro", uri=True)
oid = {r[0]: r[1] for r in np.execute("select name, oid from CHANNEL")}
num = {r[0]: r[1] for r in np.execute("select name, number from CHANNEL")}

res = collections.Counter()
bad = []
no_tag = []
for it in items:
    name = it["Name"]
    if not it.get("ImageTags", {}).get("Primary"):
        res["NO_IMAGE_TAG"] += 1
        no_tag.append((num.get(name), name))
        continue
    try:
        rendered = hashlib.md5(
            get("%s/Items/%s/Images/Primary" % (JF, it["Id"]), auth)).hexdigest()
    except Exception as e:
        res["RENDER_ERR"] += 1
        bad.append((num.get(name), name, "RENDER_ERR", str(e)))
        continue
    o = oid.get(name)
    if o is None:
        res["NOT_IN_NEXTPVR"] += 1
        bad.append((num.get(name), name, "NOT_IN_NEXTPVR", ""))
        continue
    try:
        src = hashlib.md5(get(
            "%s/service?method=channel.icon&channel_id=%s" % (NPVR, o))).hexdigest()
    except Exception as e:
        res["NPVR_ERR"] += 1
        bad.append((num.get(name), name, "NPVR_ERR", str(e)))
        continue
    if rendered == src:
        res["MATCH"] += 1
    else:
        res["MISMATCH"] += 1
        bad.append((num.get(name), name, "MISMATCH", rendered[:8] + " vs " + src[:8]))

print(json.dumps(dict(res), indent=1))
print("--- channels with no image tag ---")
for n in sorted(no_tag, key=lambda x: int(x[0]) if x[0] and str(x[0]).isdigit() else 99999):
    print(" ", n)
print("--- mismatches ---")
for b in sorted(bad, key=lambda x: int(x[0]) if x[0] and str(x[0]).isdigit() else 99999):
    print(" ", b)
