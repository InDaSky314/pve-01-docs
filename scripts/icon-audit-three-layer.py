import sqlite3, hashlib, os, json, re, sys

ROOT = "/srv/jellyfin-npvr"
JF = ROOT + "/jellyfin/config/data/jellyfin.db"
NP = ROOT + "/nextpvr/config/npvr.db3"
ICO = ROOT + "/nextpvr/config/media/channels"


def mappath(p):
    if p is None:
        return None
    if p.startswith("/config/"):
        return ROOT + "/jellyfin" + p
    return p


def h(p):
    try:
        return hashlib.md5(open(p, "rb").read()).hexdigest()
    except Exception:
        return None


def strip(n):
    return re.sub(r'[:/\\|*?"<>]', '', n)


c = sqlite3.connect("file:%s?mode=ro" % JF, uri=True)
rows = list(c.execute(
    "select b.Id, b.Name, i.Path, i.ImageType from BaseItems b "
    "left join BaseItemImageInfos i on i.ItemId=b.Id "
    "where b.Type like '%LiveTvChannel%'"))

np = sqlite3.connect("file:%s?mode=ro" % NP, uri=True)
npnum = {r[0]: r[1] for r in np.execute("select name, number from CHANNEL")}

files = {}
for f in os.listdir(ICO):
    files[os.path.splitext(f)[0]] = os.path.join(ICO, f)

byhash = {}
for name, p in files.items():
    byhash.setdefault(h(p), []).append(name)

out = []
counts = {"ok": 0, "HASH_DIFF": 0, "NO_NP_ICON": 0, "NO_JF_IMAGE": 0,
          "JF_URL": 0, "JF_FILE_MISSING": 0}
bad = []
for iid, name, path, itype in rows:
    num = npnum.get(name)
    src = files.get(strip(name))
    sh = h(src) if src else None
    real = mappath(path)
    if path and path.startswith("http"):
        st = "JF_URL"
        jh = None
    elif real is None:
        st = "NO_JF_IMAGE"
        jh = None
    else:
        jh = h(real)
        if jh is None:
            st = "JF_FILE_MISSING"
        elif sh is None:
            st = "NO_NP_ICON"
        elif jh == sh:
            st = "ok"
        else:
            st = "HASH_DIFF"
    counts[st] = counts.get(st, 0) + 1
    rec = dict(name=name, num=num, status=st, jf_path=path, jf_hash=jh,
               np_file=src, np_hash=sh,
               shows_icon_of=byhash.get(jh) if st == "HASH_DIFF" else None)
    out.append(rec)
    if st != "ok":
        bad.append(rec)

print(json.dumps(counts))
print("total", len(rows))
for r in sorted(bad, key=lambda x: (int(x["num"]) if x["num"] and str(x["num"]).isdigit() else 99999)):
    print(r["num"], "|", r["name"], "|", r["status"], "|", r["shows_icon_of"])
json.dump(out, open("/root/audit.json", "w"), indent=1)
