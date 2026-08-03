#!/usr/bin/env python3
"""Classify every channel the liveness sweep flagged, and produce the removal list.

Removal criteria, set by the owner 2026-08-03: a channel comes out only when
it is backed by BOTH research and dead air. Encoded here as:

  1. dead air TWICE  — BLACK or NO_VIDEO in the fast sweep *and* in the slow
     serial confirm pass. One failure is a measurement, not a verdict; the
     fast sweep's NO_VIDEO count jumped 3 -> 82 partway through, which is the
     shape of a provider connection limit rather than 79 simultaneous deaths.
  2. not an event-pool slot — "Soccer PPV 42", "UEFA 16", "NHL 07" and the
     Bundesliga/Live Football ranges are dark BY DESIGN between fixtures.
     Removing them would delete the working PPV system.
  3. research — recorded per group in RESEARCH below; anything without a
     citation is reported as "needs research", never auto-removed.

Outputs /root/removal-candidates.json and a summary. Deletes nothing.
"""
import json
import re
import sys

PASS1 = "/tmp/live/result.tsv"
CONFIRM = "/tmp/live/confirm.tsv"

# Dark between fixtures by design — never removal candidates.
POOL = re.compile(
    r"^(Soccer PPV|UEFA|Live Football|NHL|NBA|MLB|NFL)\s*\d+$|"
    r"SKY SPORT BUNDESLIGA|BBC STREAM|HBO MAX ORIGINAL|BIG BROTHER|"
    r"NETWORK OVERFLOW|PPV|4K$", re.I)

RESEARCH = [
    (re.compile(r"BALLY SPORTS|FANDUEL SPORTS NETWORK", re.I),
     "Bally Sports renamed FanDuel Sports Network 2024-10-21; operator "
     "Main Street Sports Group ceased operations mid-April 2026.",
     "https://www.espn.com/espn/story/_/id/48388773/"),
]


def load(path):
    out = {}
    try:
        for line in open(path):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
    except FileNotFoundError:
        pass
    return out


def main():
    p1, cf = load(PASS1), load(CONFIRM)
    if not cf:
        print("confirm pass has not run yet — refusing to produce a removal list")
        return 1

    remove, keep_pool, needs_research, recovered = [], [], [], []
    for name, s1 in p1.items():
        if s1 == "LIVE":
            continue
        s2 = cf.get(name)
        if s2 is None:
            continue
        if s2 == "LIVE":
            recovered.append(name)
            continue
        if POOL.search(name):
            keep_pool.append(name)
            continue
        cite = next(((why, url) for rx, why, url in RESEARCH if rx.search(name)), None)
        row = {"channel": name, "pass1": s1, "confirm": s2}
        if cite:
            row["research"], row["evidence_url"] = cite
            remove.append(row)
        else:
            needs_research.append(row)

    print("REMOVE (dead air twice + research) :", len(remove))
    print("event-pool slots, dark by design   :", len(keep_pool))
    print("dead air twice, NO research yet    :", len(needs_research))
    print("recovered on the confirm pass      :", len(recovered),
          "  <- these would have been false positives")
    if recovered[:8]:
        for r in recovered[:8]:
            print("      recovered:", r)
    print("\n--- needs research ---")
    for r in needs_research:
        print("   %-52s %s/%s" % (r["channel"][:52], r["pass1"], r["confirm"]))

    json.dump({"remove": remove, "needs_research": needs_research,
               "pool_kept": keep_pool, "recovered": recovered},
              open("/root/removal-candidates.json", "w"), indent=1)
    print("\nwrote /root/removal-candidates.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
