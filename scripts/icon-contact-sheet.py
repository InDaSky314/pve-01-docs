"""Contact sheet of what Jellyfin renders, ordered by channel number."""
import json, urllib.request, io, sys, sqlite3, subprocess
from PIL import Image, ImageDraw

JF = "http://192.168.9.219:8096"
KEY = "1f74eabb57a5a6165e67c08aed0108b6"
auth = {"Authorization": "MediaBrowser Token=" + KEY}
LO, HI, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]


def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=auth),
                                  timeout=30).read()


nums = json.loads(subprocess.run(
    ["sudo", "/usr/sbin/pct", "exec", "112", "--", "python3", "-c",
     "import sqlite3,json;c=sqlite3.connect('file:/srv/jellyfin-npvr/nextpvr/"
     "config/npvr.db3?mode=ro',uri=True);"
     "print(json.dumps([[r[0],r[1]] for r in c.execute('select number,name from CHANNEL')]))"],
    capture_output=True, text=True).stdout.strip().split("\n")[-1])

want = sorted([(int(n), nm) for n, nm in nums if LO <= int(n) <= HI])
items = {i["Name"]: i for i in json.loads(get(JF + "/LiveTv/Channels?limit=2000"))["Items"]}

CELL, COLS = 170, 7
rows = (len(want) + COLS - 1) // COLS
sheet = Image.new("RGB", (CELL * COLS, (CELL + 22) * rows), (30, 30, 30))
d = ImageDraw.Draw(sheet)
for k, (num, name) in enumerate(want):
    x, y = (k % COLS) * CELL, (k // COLS) * (CELL + 22)
    it = items.get(name)
    if it:
        try:
            im = Image.open(io.BytesIO(get(
                "%s/Items/%s/Images/Primary" % (JF, it["Id"])))).convert("RGB")
            im.thumbnail((CELL - 8, CELL - 8), Image.LANCZOS)
            sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
        except Exception:
            d.text((x + 6, y + 70), "ERR", fill=(255, 80, 80))
    else:
        d.text((x + 6, y + 70), "NOT IN JF", fill=(255, 80, 80))
    lbl = "%s %s" % (num, name.split(": ", 1)[-1])
    d.text((x + 3, y + CELL + 4), lbl[:30], fill=(215, 215, 215))
sheet.save(OUT)
print("wrote", OUT, len(want), "channels", sheet.size)
