#!/usr/bin/env python3
"""List CT 112 channels whose icon is a shared/generic image, for logo research.

An icon is "generic" here if its bytes are identical to at least one other
channel's icon -- 640 of 998 files share an image with something else. That is
a far better signal than file size: the earlier 5,586-byte placeholder check
only ever caught one of the 45 distinct shared images in use.

Dynamic sports event slots are excluded. Those are numbered pool channels
("NBA 07", "Soccer PPV 42", "UEFA 16") whose identity changes per event, so a
bespoke logo would be wrong by the next fixture. The owner explicitly does not
want those chased.
"""
import hashlib
import os
import re
import collections
import json

ICON_DIR = "/srv/jellyfin-npvr/nextpvr/config/media/channels"

# Numbered event-pool channels: a trailing number on a league/event name.
EVENT_SLOT = re.compile(
    r"\b(NBA|NFL|NHL|MLB|UEFA|SOCCER|FOOTBALL|PPV|FIGHT|BOXING|UFC|WWE|TENNIS|GOLF|"
    r"CRICKET|RUGBY|F1|MOTOGP|NCAA|EPL|LALIGA|SERIE A|BUNDESLIGA|SPORT[SZ]?)\b"
    r".*\b\d{1,3}\b", re.I)


def main():
    by_hash = collections.defaultdict(list)
    for name in os.listdir(ICON_DIR):
        path = os.path.join(ICON_DIR, name)
        with open(path, "rb") as fh:
            by_hash[hashlib.md5(fh.read()).hexdigest()].append(name)

    shared = {h: names for h, names in by_hash.items() if len(names) > 1}

    generic, skipped = [], []
    for h, names in shared.items():
        for n in names:
            stem = os.path.splitext(n)[0]
            (skipped if EVENT_SLOT.search(stem) else generic).append(stem)

    generic.sort()
    skipped.sort()

    print(f"total icon files      : {sum(len(v) for v in by_hash.values())}")
    print(f"unique images         : {len(by_hash)}")
    print(f"sharing an image      : {sum(len(v) for v in shared.values())}")
    print(f"  event slots skipped : {len(skipped)}")
    print(f"  worth researching   : {len(generic)}")
    print()
    print("=== CANDIDATES ===")
    for g in generic:
        print(g)

    with open("/root/icon-candidates.json", "w") as fh:
        json.dump({"candidates": generic, "skipped_event_slots": skipped}, fh, indent=1)
    print("\nwrote /root/icon-candidates.json")


if __name__ == "__main__":
    main()
