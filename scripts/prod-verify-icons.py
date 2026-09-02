"""Verify production's rendered channel artwork against the icon host.

The icon host is now the source of truth for production too, so the question
is simply: for each channel, does the image Jellyfin renders match the one the
icon host holds for that channel name?
"""
import collections, hashlib, json, re, urllib.parse, urllib.request

JF = "http://192.168.9.50:8096"
KEY = open("/etc/media-core/jellyfin-prod.key").read().strip()
HOST = "http://192.168.9.11:8100"
auth = {"Authorization": "MediaBrowser Token=" + KEY}
STRIP = re.compile(r"[:/|]")


def get(u, hdr=None, timeout=45):
    return urllib.request.urlopen(
        urllib.request.Request(u, headers=hdr or {}), timeout=timeout).read()


cat = json.loads(get(HOST + "/index.json"))
items = json.loads(get(JF + "/LiveTv/Channels?limit=2000", auth))["Items"]
print("channels:", len(items), " icon-host entries:", len(cat))

hostcache = {}
res = collections.Counter()
bad = []
for it in items:
    name, num = it["Name"], it.get("ChannelNumber")
    key = STRIP.sub("", name).strip()
    if not it.get("ImageTags", {}).get("Primary"):
        res["NO_IMAGE"] += 1
        bad.append((num, name, "NO_IMAGE"))
        continue
    try:
        rendered = hashlib.md5(get("%s/Items/%s/Images/Primary" % (JF, it["Id"]),
                                   auth)).hexdigest()
    except Exception as e:
        res["RENDER_ERR"] += 1
        bad.append((num, name, "RENDER_ERR " + str(e)[:40]))
        continue
    path = cat.get(key)
    if path is None:
        res["NOT_ON_ICON_HOST"] += 1
        bad.append((num, name, "NOT_ON_ICON_HOST"))
        continue
    if path not in hostcache:
        hostcache[path] = hashlib.md5(get(HOST + path)).hexdigest()
    if rendered == hostcache[path]:
        res["MATCH"] += 1
    else:
        res["STALE"] += 1
        bad.append((num, name, "STALE"))

print(json.dumps(dict(res), indent=1))
for b in sorted(bad, key=lambda x: int(x[0]) if x[0] and str(x[0]).isdigit() else 99999):
    print(" ", b)
