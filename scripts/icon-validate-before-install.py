import os, re, sqlite3, hashlib, json, sys
ICO="/srv/jellyfin-npvr/nextpvr/config/media/channels"
NEW="/root/agy-icons-final"
STRIP=re.compile(r'[:/\\|*?"<>]')
np=sqlite3.connect("file:/srv/jellyfin-npvr/nextpvr/config/npvr.db3?mode=ro",uri=True)
chan={STRIP.sub("",r[0]): r[0] for r in np.execute("select name from CHANNEL")}
ok=bad=0
for f in sorted(os.listdir(NEW)):
    if not f.endswith(".png"): continue
    base=f[:-4]
    problems=[]
    if base not in chan: problems.append("NO MATCHING CHANNEL")
    if os.path.exists(os.path.join(ICO, base+".jpg")): problems.append("JPG TWIN EXISTS")
    if os.path.getsize(os.path.join(NEW,f)) < 2000: problems.append("SUSPICIOUSLY SMALL")
    if problems:
        bad+=1; print("FAIL", f, problems)
    else:
        ok+=1; print("ok  ", chan[base])
print("\n%d ok, %d problems" % (ok,bad))
sys.exit(1 if bad else 0)
