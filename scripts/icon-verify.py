#!/usr/bin/env python3
"""Compare a stack's live channel artwork against the archive, and repair it.

Run after any operation that can make Jellyfin or NextPVR re-fetch channel
images -- a guide refresh, a channel re-import, a cache clear. Those are the
moments artwork silently degrades: the provider has replaced many logo URLs
with the DirecTV GO placeholder, so anything that re-fetches pulls the worse
image over the better one that was cached earlier.

Reports three things per channel:

  same      live image matches the archive
  degraded  live image is now a shared placeholder where the archive has
            unique artwork -- this is the loss case worth repairing
  improved  live image is unique where the archive had a placeholder, i.e.
            the archive is the stale one and should be refreshed

Only `degraded` is a problem. Repair writes the archived image into NextPVR's
icon directory, which is the one place a file can be put back; Jellyfin then
picks it up on its next channel image fetch.

  icon-verify.py check <stack>
  icon-verify.py repair <stack>      (nextpvr stacks only)
"""
import collections
import hashlib
import json
import os
import re
import subprocess
import sys

ARCHIVE = "/root/icon-archive"
BLOBS = os.path.join(ARCHIVE, "blobs")
MANIFEST = os.path.join(ARCHIVE, "manifest.json")
PLACEHOLDER_MIN_SHARE = 5

NPVR_DIR = {"ct112-nextpvr": ("112", "/srv/jellyfin-npvr/nextpvr/config/media/channels")}


def live_images(stack_name):
    """Re-read current artwork using icon-archive's own collectors."""
    out = subprocess.run(["/usr/local/bin/icon-archive", "export"],
                         capture_output=True, text=True, timeout=900)
    if out.returncode:
        raise RuntimeError(out.stderr[:300])
    m = json.load(open(MANIFEST))
    return m["stacks"][stack_name]["entries"]


def compare(stack_name, before):
    after = live_images(stack_name)
    norm = lambda s: re.sub(r"[:/|]", "", s).strip()
    b = {norm(k): v for k, v in before.items()}
    a = {norm(k): v for k, v in after.items()}

    same = degraded = improved = 0
    losses = []
    for ch, av in a.items():
        bv = b.get(ch)
        if not bv:
            continue
        if av["md5"] == bv["md5"]:
            same += 1
        elif av["placeholder"] and not bv["placeholder"]:
            degraded += 1
            losses.append((ch, bv))
        elif bv["placeholder"] and not av["placeholder"]:
            improved += 1
    print(f"  unchanged : {same}")
    print(f"  DEGRADED  : {degraded}   (archive has better art)")
    print(f"  improved  : {improved}   (archive is stale for these)")
    return losses


def repair(stack_name, losses):
    if stack_name not in NPVR_DIR:
        print(f"repair only supported for NextPVR stacks, not {stack_name}")
        return 1
    ct, dest = NPVR_DIR[stack_name]
    n = 0
    for ch, entry in losses:
        blob = os.path.join(BLOBS, entry["md5"])
        if not os.path.exists(blob):
            continue
        tmp = f"/tmp/icon-repair-{entry['md5']}.png"
        subprocess.run(["cp", blob, tmp], check=True)
        subprocess.run(["/usr/sbin/pct", "push", ct, tmp,
                        os.path.join(dest, ch + ".png")], check=True)
        os.unlink(tmp)
        n += 1
    print(f"restored {n} images into {stack_name}")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    stack = sys.argv[2] if len(sys.argv) > 2 else "production-jellyfin"
    if not os.path.exists(MANIFEST):
        print("no archive -- run icon-archive export first", file=sys.stderr)
        return 1
    before = json.load(open(MANIFEST))["stacks"][stack]["entries"]
    print(f"comparing {stack} against archive ({len(before)} channels)")
    losses = compare(stack, before)
    if cmd == "repair" and losses:
        return repair(stack, losses)
    if losses:
        print(f"\n{len(losses)} channels lost artwork; run: "
              f"icon-verify.py repair {stack}")
        for ch, e in losses[:10]:
            print(f"   {ch[:44]:<44} archived {e['bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
