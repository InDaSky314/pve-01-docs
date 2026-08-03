import sqlite3, os, shutil, json

DB = "/srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db"
ROOT = "/srv/jellyfin-npvr/jellyfin"

c = sqlite3.connect(DB)
q = ("select i.Id, i.Path from BaseItems b join BaseItemImageInfos i "
     "on i.ItemId=b.Id where b.Type like '%LiveTvChannel%'")
rows = list(c.execute(q))
print("channel image rows:", len(rows))

removed_files = 0
removed_dirs = 0
for _id, p in rows:
    real = ROOT + p if p.startswith("/config/") else p
    if os.path.isfile(real):
        os.remove(real)
        removed_files += 1
    # the per-item dir is <...>/livetv/<guid>/ ; only prune if now empty
    d = os.path.dirname(real)
    for _ in range(2):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                removed_dirs += 1
                d = os.path.dirname(d)
            else:
                break
        except OSError:
            break

ids = [r[0] for r in rows]
c.executemany("delete from BaseItemImageInfos where Id=?", [(i,) for i in ids])
c.commit()
left = list(c.execute("select count(*) from BaseItemImageInfos i "
                      "join BaseItems b on i.ItemId=b.Id "
                      "where b.Type like '%LiveTvChannel%'"))
print("files removed:", removed_files, "dirs pruned:", removed_dirs)
print("channel image rows remaining:", left)
