import json, urllib.request, collections, datetime, sys
BASE, KEY = sys.argv[1], sys.argv[2]
def api(p):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE+p, headers={"Authorization":"MediaBrowser Token="+KEY}), timeout=120).read())
now = datetime.datetime.now(datetime.timezone.utc)
end = now + datetime.timedelta(hours=24)
chans = api("/LiveTv/Channels?limit=2000")["Items"]
progs = api("/LiveTv/Programs?limit=100000&MinEndDate=%s&MaxStartDate=%s"
            % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")))["Items"]
per = collections.Counter(p["ChannelId"] for p in progs)
b = collections.Counter()
zero = []
for c in chans:
    n = per.get(c["Id"], 0)
    b["0" if n == 0 else "1-2" if n < 3 else "3-9" if n < 10 else "10+"] += 1
    if n == 0:
        zero.append((c.get("ChannelNumber"), c["Name"]))
print(BASE)
print(" channels:", len(chans), " programmes in next 24h:", len(progs))
print(" per-channel:", dict(b))
print(" channels with NO guide in next 24h:", len(zero))
for z in sorted(zero, key=lambda x: int(x[0]) if x[0] and x[0].isdigit() else 99999)[:20]:
    print("   ", z)
