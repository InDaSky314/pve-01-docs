"""Do both stacks render the same artwork for the same channel?"""
import json, hashlib, urllib.request, re
def api(base, key, path):
    return urllib.request.urlopen(urllib.request.Request(
        base+path, headers={"Authorization":"MediaBrowser Token="+key}), timeout=60).read()
P=("http://192.168.9.50:8096","3f579d403112dfbe5c2dd69832c5cbfe")
C=("http://192.168.9.219:8096","1f74eabb57a5a6165e67c08aed0108b6")
def grab(s):
    items=json.loads(api(s[0],s[1],"/LiveTv/Channels?limit=2000"))["Items"]
    out={}
    for i in items:
        try:
            out[i["Name"]]=hashlib.md5(api(s[0],s[1],"/Items/%s/Images/Primary"%i["Id"])).hexdigest()
        except Exception:
            out[i["Name"]]="ERR"
    return out
p=grab(P); c=grab(C)
common=set(p)&set(c)
same=[n for n in common if p[n]==c[n]]
diff=[n for n in common if p[n]!=c[n]]
print("production:",len(p)," ct112:",len(c)," common:",len(common))
print("IDENTICAL artwork:",len(same)," DIFFERENT:",len(diff))
for n in sorted(diff)[:30]: print("  ",n,p[n][:8],"vs",c[n][:8])
print("only in production:",sorted(set(p)-set(c))[:10])
print("only in ct112:",sorted(set(c)-set(p))[:10])
