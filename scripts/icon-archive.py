#!/usr/bin/env python3
"""Archive and catalogue channel artwork from every stack that holds any.

Why this exists
---------------
Production's best channel artwork exists in exactly one place: Jellyfin's
image cache on CT 105. It is not at the provider (those URLs have since been
degraded to the DirecTV GO placeholder), not in Threadfin, and not on disk
anywhere else. Jellyfin only still has it because `x-update-channel-icon` is
False and it never re-fetches. Verified 2026-08-02: 14 channels -- including
Green Bay ABC 2, Big Ten and ESPN -- render real logos whose provider URL now
returns the placeholder.

That makes a Jellyfin cache wipe an irreversible loss of artwork. CT 112 had
exactly that happen this morning, and there the files were recoverable from
NextPVR. On production there would be nothing to recover from.

CT 112's NextPVR artwork is also archived here: 83 real logos sourced from
tv-logos plus generated ones, likewise only on that one disk.

Design
------
* Jellyfin records an image Path as a local file OR a remote URL; both are
  archived, the remote ones by fetching. Archiving only local files missed
  most of what renders (CT 112 keeps 856 of 997 as NextPVR URLs).
* Keyed by **channel name**, never by number. Renumbering changes numbers and
  leaves names alone, so an archive keyed by name survives it.
* Placeholders are catalogued but flagged, not silently kept as if custom.
  An image shared by many channels is a stock placeholder by definition; the
  DirecTV GO one is 5,586 bytes and shared by 134 channels.
* Content-addressed: files are stored once by md5, and the manifest maps
  channel names onto them, so duplicates cost nothing.
* Writes only to the archive directory. Never modifies a stack.

Usage
-----
  icon-archive.py export            snapshot every stack into the archive
  icon-archive.py list              summarise what is archived
  icon-archive.py extract <dir> [stack]
                                    write archived art out as name-keyed PNGs,
                                    ready to drop into a NextPVR icon dir or
                                    feed a rebuild
"""
import collections
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

ARCHIVE = "/root/icon-archive"
BLOBS = os.path.join(ARCHIVE, "blobs")
MANIFEST = os.path.join(ARCHIVE, "manifest.json")

# A stack is either a Jellyfin instance (art lives in the database's image
# rows) or a NextPVR instance (art lives as name-keyed files on disk).
STACKS = [
    {"name": "production-jellyfin", "ct": "105", "kind": "jellyfin",
     "db": "/srv/media-core/jellyfin/config/data/jellyfin.db",
     "container_prefix": "/config",
     "host_prefix": "/srv/media-core/jellyfin/config"},
    {"name": "ct112-nextpvr", "ct": "112", "kind": "nextpvr",
     "dir": "/srv/jellyfin-npvr/nextpvr/config/media/channels"},
    # Generated artwork that is not installed anywhere yet. Without this the
    # archive would miss it entirely -- it only ever snapshotted live stacks,
    # and an agy run destroyed 102 of its own generated icons on 2026-08-02
    # before any of them reached a stack.
    {"name": "generated-pending", "ct": None, "kind": "hostdir",
     "dir": "/root/agy-icons-keep"},
    {"name": "ct112-jellyfin", "ct": "112", "kind": "jellyfin",
     "db": "/srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db",
     "container_prefix": "/config",
     "host_prefix": "/srv/jellyfin-npvr/jellyfin/config"},
]

# Anything shared by at least this many channels is stock, not custom.
PLACEHOLDER_MIN_SHARE = 5


def pct(ct, *args, binary=False):
    r = subprocess.run(["/usr/sbin/pct", "exec", ct, "--", *args],
                       capture_output=True, timeout=180)
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[:200])
    return r.stdout if binary else r.stdout.decode()


def collect_jellyfin(stack):
    """(channel name -> raw bytes) from a Jellyfin instance's image rows."""
    script = f"""
import sqlite3, os, json, base64
c = sqlite3.connect('file:{stack["db"]}?mode=ro', uri=True, timeout=30)
out = []
for name, path in c.execute(
        "select b.Name, i.Path from BaseItemImageInfos i "
        "join BaseItems b on b.Id = i.ItemId "
        "where b.type like '%LiveTvChannel%'"):
    if not path:
        continue
    # Jellyfin stores this as either a local file or a remote URL. Production
    # is 730 local / 252 remote; CT 112 is 141 local / 856 remote pointing at
    # NextPVR. Archiving only the local half would miss most of what actually
    # renders, so remote entries are fetched.
    if path.startswith('http'):
        try:
            import urllib.request
            req = urllib.request.Request(path, headers={{'User-Agent': 'icon-archive'}})
            with urllib.request.urlopen(req, timeout=20) as r:
                blob = r.read()
        except Exception:
            continue
        if len(blob) < 200:
            continue
        out.append([name, base64.b64encode(blob).decode()])
        continue
    real = path.replace('{stack["container_prefix"]}', '{stack["host_prefix"]}', 1)
    if not os.path.exists(real):
        continue
    with open(real, 'rb') as fh:
        out.append([name, base64.b64encode(fh.read()).decode()])
print(json.dumps(out))
"""
    import base64
    raw = pct(stack["ct"], "python3", "-c", script)
    return {n: base64.b64decode(b) for n, b in json.loads(raw)}


def collect_hostdir(stack):
    """(channel name -> raw bytes) from a plain directory on the host."""
    d = stack["dir"]
    if not os.path.isdir(d):
        raise RuntimeError(f"{d} does not exist")
    out = {}
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                out[os.path.splitext(f)[0]] = fh.read()
    return out


def collect_nextpvr(stack):
    """(channel name -> raw bytes) from NextPVR's name-keyed icon directory."""
    script = f"""
import os, json, base64
d = '{stack["dir"]}'
out = []
for f in os.listdir(d):
    p = os.path.join(d, f)
    if not os.path.isfile(p):
        continue
    with open(p, 'rb') as fh:
        out.append([os.path.splitext(f)[0], base64.b64encode(fh.read()).decode()])
print(json.dumps(out))
"""
    import base64
    raw = pct(stack["ct"], "python3", "-c", script)
    return {n: base64.b64decode(b) for n, b in json.loads(raw)}


def export():
    os.makedirs(BLOBS, exist_ok=True)
    manifest = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stacks": {}}

    for stack in STACKS:
        try:
            if stack["kind"] == "jellyfin":
                imgs = collect_jellyfin(stack)
            elif stack["kind"] == "hostdir":
                imgs = collect_hostdir(stack)
            else:
                imgs = collect_nextpvr(stack)
        except Exception as exc:                              # noqa: BLE001
            # Carry forward the previous good entry rather than replacing it
            # with an error stub -- a transient failure must not erase the
            # catalogue of what was archived last time.
            print(f"  {stack['name']}: SKIPPED ({exc})")
            prior = (json.load(open(MANIFEST)).get("stacks", {}).get(stack["name"])
                     if os.path.exists(MANIFEST) else None)
            if prior and "entries" in prior:
                prior["stale"] = True
                manifest["stacks"][stack["name"]] = prior
                print(f"      kept previous catalogue ({prior['channels']} channels)")
            else:
                manifest["stacks"][stack["name"]] = {"error": str(exc)}
            continue

        # Frequency first: an image shared across many channels is stock.
        freq = collections.Counter(hashlib.md5(b).hexdigest() for b in imgs.values())

        entries = {}
        for name, blob in imgs.items():
            md5 = hashlib.md5(blob).hexdigest()
            dest = os.path.join(BLOBS, md5)
            if not os.path.exists(dest):
                with open(dest, "wb") as fh:
                    fh.write(blob)
            entries[name] = {
                "md5": md5,
                "bytes": len(blob),
                "shared_by": freq[md5],
                "placeholder": freq[md5] >= PLACEHOLDER_MIN_SHARE,
            }

        custom = sum(1 for e in entries.values() if not e["placeholder"])
        manifest["stacks"][stack["name"]] = {
            "ct": stack["ct"], "kind": stack["kind"],
            "channels": len(entries), "custom": custom,
            "placeholder": len(entries) - custom,
            "entries": entries,
        }
        print(f"  {stack['name']:<22} {len(entries):>4} channels, "
              f"{custom:>4} custom, {len(entries)-custom:>4} placeholder")

    with open(MANIFEST, "w") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)

    blobs = len(os.listdir(BLOBS))
    size = sum(os.path.getsize(os.path.join(BLOBS, f)) for f in os.listdir(BLOBS))
    print(f"\narchive: {blobs} unique images, {size/1048576:.1f} MB")
    print(f"manifest: {MANIFEST}")
    return 0


def show():
    if not os.path.exists(MANIFEST):
        print("no archive yet -- run: icon-archive.py export")
        return 1
    m = json.load(open(MANIFEST))
    print(f"archived {m['generated']}\n")
    for name, s in m["stacks"].items():
        if "error" in s:
            print(f"  {name:<22} ERROR {s['error'][:60]}")
            continue
        print(f"  {name:<22} {s['channels']:>4} channels  "
              f"{s['custom']:>4} custom  {s['placeholder']:>4} placeholder")
    # channels whose art exists on only one stack are the fragile ones
    where = collections.defaultdict(set)
    for sname, s in m["stacks"].items():
        for ch, e in (s.get("entries") or {}).items():
            if not e["placeholder"]:
                where[ch].add(sname)
    only = [c for c, v in where.items() if len(v) == 1]
    print(f"\ncustom artwork existing on only ONE stack: {len(only)}")
    return 0


def extract(dest, stack_pref=None, custom_only=True):
    """Write archived art out as name-keyed PNGs, ready to install.

    Filenames strip `:` `/` `|` because that is what NextPVR looks for -- the
    rule that silently hid 36 of the first 37 installs, and later the last 2
    of 997.

    Where several stacks hold art for the same channel, the largest image
    wins; the bigger file has consistently been the better artwork here.
    """
    import re
    m = json.load(open(MANIFEST))
    order = ([stack_pref] if stack_pref else
             ["ct112-nextpvr", "generated-pending",
              "production-jellyfin", "ct112-jellyfin"])
    best, aliases = {}, {}
    PREFIX = re.compile(r"^(US|UK|DE|CA|IT|FR|ES|NL|PT|PL|TR|AR|BR|MX)\s+", re.I)
    for sname in order:
        s = m["stacks"].get(sname) or {}
        for ch, e in (s.get("entries") or {}).items():
            if custom_only and e["placeholder"]:
                continue
            # Key on the STRIPPED name, because that is the filename that
            # will be written. "Madison: CBS 3 (WISC)" from Jellyfin and
            # "Madison CBS 3 (WISC)" from NextPVR are the same channel and
            # must compete, not silently overwrite each other.
            # Two stacks name the same channel differently: production says
            # "US: CNN 4K", NextPVR says "CNN 4K". Keyed literally they look
            # like different channels, so each keeps its own art -- which is
            # how a contaminated production copy survived beside the correct
            # NextPVR one. Group on the prefix-stripped name so they compete,
            # then write the winner out under every alias seen.
            flat = re.sub(r"[:/|]", "", ch).strip()
            key = PREFIX.sub("", flat).strip().upper()
            aliases.setdefault(key, set()).add(flat)
            # STRICT source precedence, never size. Largest-wins let a
            # contaminated production copy beat CT 112's correct file: on
            # 2026-08-02 an archive run happened while production's channel
            # images were misaligned, so CNN's logo was captured as LAFF TV's
            # artwork and then served back as the source of truth. NextPVR's
            # files are name-keyed on disk and cannot drift that way, so they
            # win outright; generated artwork next; Jellyfin's cache last,
            # because it is the only source that can silently mis-associate.
            if key not in best:
                best[key] = e
    os.makedirs(dest, exist_ok=True)
    written = 0
    for key, e in best.items():
        blob = os.path.join(BLOBS, e["md5"])
        if not os.path.exists(blob):
            continue
        for alias in aliases.get(key, {key}):
            shutil.copy2(blob, os.path.join(dest, alias + ".png"))
            written += 1
    print(f"extracted {written} images to {dest}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "export"
    if cmd == "export":
        sys.exit(export())
    if cmd == "extract":
        dest = sys.argv[2] if len(sys.argv) > 2 else "/root/icon-archive/extracted"
        stack = sys.argv[3] if len(sys.argv) > 3 else None
        sys.exit(extract(dest, stack))
    sys.exit(show())
