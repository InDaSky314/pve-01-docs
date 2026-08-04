#!/usr/bin/env python3
"""Import channels into NextPVR from the current playlist.

Traps this respects, all from nextpvr-cli.md and all previously paid for:
  * m3u=existing does NOT re-read the file -- pass the real path.
  * save is a POST, with the channels array from the scan status as the body.
  * groups is a keyword ("all"/"none"), not a list.
  * scan status is CONSUMED by reading it: poll once, keep the payload.
"""
import hashlib, json, re, sys, time, urllib.parse, urllib.request

B = "http://localhost:8866"
PIN = "0000"


def get(p, timeout=180):
    try:
        return urllib.request.urlopen(B + p, timeout=timeout).read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        print("HTTP %s on %s\n%s" % (e.code, p.split("&sid=")[0][:110], body[:400]))
        raise


def post(p, body, timeout=300):
    req = urllib.request.Request(B + p, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout).read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        print("HTTP %s on save:\n%s" % (e.code, e.read().decode(errors="ignore")[:400]))
        raise


x = get("/service?method=session.initiate&ver=1.0&device=reimport")
sid = re.search(r"<sid>([^<]+)</sid>", x).group(1)
salt = re.search(r"<salt>([^<]+)</salt>", x).group(1)
md5 = hashlib.md5((":" + hashlib.md5(PIN.encode()).hexdigest() + ":" + salt).encode()).hexdigest()
get("/service?method=session.login&md5=%s&sid=%s" % (md5, sid))

src = sys.argv[1] if len(sys.argv) > 1 else "1"
m3u = urllib.parse.quote("/config/playlist.m3u", safe="")
xml = urllib.parse.quote("/config/epg.xml", safe="")
print("scan.start:", get("/services/service?method=setting.scan.start&format=json"
                         "&source_id=%s&m3u=%s&xmltv=%s&sid=%s" % (src, m3u, xml, sid))[:160])

payload = None
for _ in range(60):
    time.sleep(5)
    s = get("/services/service?method=setting.scan.status&format=json&sid=" + sid)
    try:
        d = json.loads(s)
    except Exception:
        continue
    if d.get("complete") or d.get("channels"):
        payload = d
        break
    print("   ...", str(d)[:90])

if not payload or not payload.get("channels"):
    print("no channel list returned; payload:", str(payload)[:300])
    sys.exit(1)
print("scanned channels:", len(payload["channels"]))
json.dump(payload, open("/root/last-scan.json", "w"))   # status is consumed by reading
# The body is the bare channels array. Wrapping it in {"channels": [...]}
# returns 500 -- the shape the status endpoint hands back is the shape save
# expects.
r = post("/services/service?method=setting.scan.save&format=json&groups=all&sid=" + sid,
         payload["channels"])
print("scan.save:", r[:200])
