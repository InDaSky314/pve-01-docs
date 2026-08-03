import sqlite3, os, shutil

DB = "/srv/media-core/jellyfin/config/data/jellyfin.db"
ROOT = "/srv/media-core/jellyfin"

c = sqlite3.connect(DB)
q = ("select i.Id, i.Path from BaseItems b join BaseItemImageInfos i "
     "on i.ItemId=b.Id where b.Type like '%LiveTvChannel%'")
rows = list(c.execute(q))
print("channel image rows:", len(rows))

files = dirs = 0
for _id, p in rows:
    if p.startswith("http"):
        continue
    real = ROOT + p if p.startswith("/config/") else p
    if os.path.isfile(real):
        os.remove(real)
        files += 1
    d = os.path.dirname(real)
    for _ in range(2):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d); dirs += 1; d = os.path.dirname(d)
            else:
                break
        except OSError:
            break

c.executemany("delete from BaseItemImageInfos where Id=?",
              [(r[0],) for r in rows])
c.commit()
left = list(c.execute("select count(*) from BaseItemImageInfos i "
                      "join BaseItems b on i.ItemId=b.Id "
                      "where b.Type like '%LiveTvChannel%'"))
print("files removed:", files, "dirs pruned:", dirs)
print("channel image rows remaining:", left)
