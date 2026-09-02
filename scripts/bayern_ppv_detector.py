#!/usr/bin/env python3
"""
Bayern Munich English PPV Match Detector & Scheduler Prototype.

Queries:
1. OpenLigaDB (for German Bundesliga and DFB-Pokal) and ESPN Public Soccer API
   (for UEFA Champions League) for upcoming Bayern Munich fixtures.
2. Xtream Codes API (via CT 105 Swiss egress) for target English PPV categories:
   - Category 573: US| DAZN PPV
   - Category 575: UK| DAZN PPV VIP
   - Category 1441: UK| SKY SPORT+ PPV
   - Category 1811: UK| DAZN PPV
   (and optional DE categories as fallback)

Matching Logic:
- Multi-tier regex matching for Bayern Munich team names.
- Negative regex filtering to eliminate false positives (Women, Amateure, Basketball, Highlights, Replays).
- Cross-referencing opponent name and kickoff timestamp from OpenLigaDB / ESPN schedule.
- Category market prioritization (US English > UK English > DE German).
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Xtream API details (CT 105 credentials)
XTREAM_BASE = "http://cf.teltv.xyz"
XTREAM_USER = "c21fa33845e9"
XTREAM_PASS = "3e110912e1"

# Target Categories
TARGET_CATEGORIES = {
    573: {"name": "US| DAZN PPV", "lang": "EN-US", "priority": 100},
    575: {"name": "UK| DAZN PPV VIP", "lang": "EN-UK", "priority": 90},
    1441: {"name": "UK| SKY SPORT+ PPV", "lang": "EN-UK", "priority": 85},
    1811: {"name": "UK| DAZN PPV", "lang": "EN-UK", "priority": 80},
    # German fallback categories
    1137: {"name": "DE| BUNDESLIGA HD/4K", "lang": "DE", "priority": 40},
    433: {"name": "DE| DAZN PPV", "lang": "DE", "priority": 30},
}

# Regex patterns for team matching
BAYERN_PATTERNS = [
    re.compile(r"\b(Bayern\s+M[uü]nchen|Bayern\s+Munich|FC\s+Bayern|Bayern)\b", re.IGNORECASE),
]

FALSE_POSITIVES = re.compile(
    r"\b(women|frauen|amateure|u19|u17|basketball|bbl|highlights|replay|magazine)\b|\b(?:FC\s+)?Bayern(?:\s+M[uü]nchen|\s+Munich)?\s+II\b",
    re.IGNORECASE
)

OPENLIGADB_BASE = "https://api.openligadb.de/getmatchdata"
USER_AGENT_HTTP = "MediaCoreSync/1.0"
USER_AGENT_ESPN = "curl/7.88.1"


class ProviderFetchError(Exception):
    """Raised when fetching streams from IPTV provider fails."""


class FixtureFetchError(Exception):
    """Raised when fetching fixture schedules fails."""


def fetch_bayern_fixtures() -> tuple[list[dict], list[str]]:
    """
    Fetch upcoming Bayern Munich fixtures from OpenLigaDB (Bundesliga & DFB-Pokal)
    and ESPN (UEFA Champions League).
    Returns (sorted_fixtures, errors_list).
    """
    fixtures = []
    errors = []
    now = datetime.now(timezone.utc)
    season_year = now.year if now.month >= 7 else now.year - 1

    # 1. OpenLigaDB for Bundesliga (bl1) and DFB-Pokal (dfb)
    oldb_leagues = [
        ("bl1", f"{OPENLIGADB_BASE}/bl1/{season_year}", "German Bundesliga"),
        ("dfb", f"{OPENLIGADB_BASE}/dfb/{season_year}", "DFB-Pokal"),
    ]

    for league_key, url, league_name in oldb_leagues:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT_HTTP})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
                if isinstance(data, list):
                    for m in data:
                        t1 = m.get("team1") or {}
                        t2 = m.get("team2") or {}
                        t1_name = t1.get("teamName", "")
                        t2_name = t2.get("teamName", "")
                        if not ("Bayern" in t1_name or "Bayern" in t2_name):
                            continue

                        dt_utc = m.get("matchDateTimeUTC")
                        if not dt_utc:
                            continue
                        try:
                            kickoff = datetime.fromisoformat(dt_utc.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            continue

                        # Include matches that are upcoming or started within the last 3 hours
                        if kickoff < now - timedelta(hours=3):
                            continue

                        is_home = "Bayern" in t1_name
                        opponent = t2_name if is_home else t1_name
                        fixtures.append({
                            "id": f"oldb_{m.get('matchID')}",
                            "league": league_name,
                            "match_name": f"{t1_name} vs. {t2_name}",
                            "short_name": f"{t1.get('shortName') or t1_name} vs. {t2.get('shortName') or t2_name}",
                            "kickoff": kickoff,
                            "opponent": opponent,
                            "is_home": is_home,
                        })
                else:
                    errors.append(f"OpenLigaDB {league_key} returned non-list data")
        except Exception as exc:
            errors.append(f"OpenLigaDB {league_key} error: {exc}")

    # 2. ESPN for UEFA Champions League (teams schedule + scoreboard)
    for s in [season_year - 1, season_year, season_year + 1]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/teams/132/schedule?season={s}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT_ESPN})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            for ev in data.get("events", []):
                dt_str = ev.get("date")
                if not dt_str:
                    continue
                try:
                    kickoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if kickoff < now - timedelta(hours=3):
                    continue
                name = ev.get("name", "")
                short_name = ev.get("shortName", "")
                comps_list = ev.get("competitions", [])
                comps = comps_list[0] if comps_list else {}
                competitors = comps.get("competitors", [])
                opponent = "Unknown"
                is_home = True
                for team_info in competitors:
                    tname = team_info.get("team", {}).get("displayName", "")
                    tid = str(team_info.get("id") or team_info.get("team", {}).get("id", ""))
                    if tid != "132" and "Bayern" not in tname:
                        opponent = tname
                    else:
                        is_home = team_info.get("homeAway") == "home"
                fixtures.append({
                    "id": f"espn_{ev.get('id')}",
                    "league": "UEFA Champions League",
                    "match_name": name,
                    "short_name": short_name,
                    "kickoff": kickoff,
                    "opponent": opponent,
                    "is_home": is_home,
                })
        except Exception:
            # ESPN UCL schedule query is best-effort
            pass

    # Scoreboard for near-term UCL fixtures
    url_sb = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard"
    try:
        req = urllib.request.Request(url_sb, headers={"User-Agent": USER_AGENT_ESPN})
        with urllib.request.urlopen(req, timeout=10) as resp:
            sb_data = json.load(resp)
        for ev in sb_data.get("events", []):
            name = ev.get("name", "")
            if "Bayern" in name:
                dt_str = ev.get("date")
                kickoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if kickoff >= now - timedelta(hours=3):
                    comps_list = ev.get("competitions", [])
                    comps = comps_list[0] if comps_list else {}
                    competitors = comps.get("competitors", [])
                    opponent = "Unknown"
                    for team_info in competitors:
                        tname = team_info.get("team", {}).get("displayName", "")
                        tid = str(team_info.get("id") or team_info.get("team", {}).get("id", ""))
                        if tid != "132" and "Bayern" not in tname:
                            opponent = tname
                    fixtures.append({
                        "id": f"espn_sb_{ev.get('id')}",
                        "league": "UEFA Champions League",
                        "match_name": name,
                        "short_name": ev.get("shortName", ""),
                        "kickoff": kickoff,
                        "opponent": opponent,
                        "is_home": True,
                    })
    except Exception:
        pass

    # Deduplicate by fixture ID and sort by kickoff
    unique = {}
    for f in fixtures:
        unique[f["id"]] = f
    sorted_fixtures = sorted(unique.values(), key=lambda x: x["kickoff"])
    return sorted_fixtures, errors


def fetch_espn_bayern_schedule() -> list[dict]:
    """Compatibility alias: calls fetch_bayern_fixtures and returns list of fixtures."""
    fixtures, _ = fetch_bayern_fixtures()
    return fixtures


def fetch_provider_ppv_streams(cat_id: int) -> list[dict]:
    """
    Fetch live streams for a specific provider category.
    Runs via CT 105's Swiss egress with User-Agent: MediaCoreSync/1.0.
    """
    url = f"{XTREAM_BASE}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams&category_id={cat_id}"

    # If running directly inside CT 105:
    is_in_container = os.path.exists("/srv/media-core/.env") and not os.path.exists("/usr/sbin/pct")

    if is_in_container:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT_HTTP})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
                if isinstance(data, list):
                    return data
                raise ProviderFetchError(f"Category {cat_id} returned unexpected data type: {type(data).__name__}")
        except Exception as exc:
            raise ProviderFetchError(f"Failed fetching category {cat_id} inside CT 105: {exc}") from exc
    else:
        # On host: shell provider query into CT 105
        cmd = [
            "/usr/sbin/pct", "exec", "105", "--",
            "curl", "-s", "--max-time", "15",
            "-A", USER_AGENT_HTTP,
            url
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if res.returncode != 0:
                raise ProviderFetchError(f"pct exec 105 failed (exit {res.returncode}): {res.stderr.strip()}")
            out = res.stdout.strip()
            if not out:
                raise ProviderFetchError(f"Empty response received for category {cat_id}")
            data = json.loads(out)
            if isinstance(data, list):
                return data
            raise ProviderFetchError(f"Category {cat_id} returned non-list JSON: {type(data).__name__}")
        except subprocess.TimeoutExpired as exc:
            raise ProviderFetchError(f"Timeout querying category {cat_id} via CT 105: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderFetchError(f"Invalid JSON from category {cat_id}: {exc} (response: {out[:100]})") from exc
        except Exception as exc:
            raise ProviderFetchError(f"Error querying category {cat_id} via CT 105: {exc}") from exc


def extract_channel_slot(raw_name: str) -> str:
    """Extract channel slot label from stream title (e.g. 'US: DAZN PPV 36' or 'GB: DAZN PPV 34')."""
    m = re.search(r"\|\s*([A-Z]{2}:\s*[^|]+)$", raw_name, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"\b([A-Z]{2}:\s*[^|]+)$", raw_name, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    return raw_name.strip()


def find_best_matching_fixture(stream_name: str, fixtures: list[dict]) -> dict | None:
    """Find the most relevant fixture from the schedule for a given stream name."""
    if not fixtures:
        return None
    # 1. Match opponent keyword in stream name
    for f in fixtures:
        opp = f.get("opponent", "")
        opp_keywords = [w for w in re.findall(r"\w+", opp) if len(w) > 3 and w.lower() not in ["fc", "vfb", "tsv", "1.fc", "sv", "sc", "vfl"]]
        for kw in opp_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", stream_name, re.IGNORECASE):
                return f
    # 2. Match fixture kickoff date
    for f in fixtures:
        f_date_str = f["kickoff"].strftime("%Y-%m-%d")
        if f_date_str in stream_name:
            return f
    # Default to first upcoming fixture
    return fixtures[0]


def match_stream_generic(stream, cat_info, team_patterns, false_positives_pattern=None, fixture=None, espn_fixture=None):
    """
    Generic evaluator for provider stream match criteria.
    Accepts fixture (or espn_fixture for backward compatibility).
    Returns match dictionary if matched, else None.
    """
    target_fixture = fixture or espn_fixture
    raw_name = stream.get("name", "")
    stream_id = stream.get("stream_id")

    if false_positives_pattern and false_positives_pattern.search(raw_name):
        return None

    matched_team = False
    for p in team_patterns:
        if p.search(raw_name):
            matched_team = True
            break
    if not matched_team:
        return None

    score = cat_info["priority"]
    match_reasons = [f"Category: {cat_info['name']} (+{cat_info['priority']} pts)"]

    if target_fixture:
        opp = target_fixture.get("opponent", "")
        opp_keywords = [w for w in re.findall(r"\w+", opp) if len(w) > 3 and w.lower() not in ["fc", "vfb", "tsv", "1.fc", "sv", "sc", "vfl"]]
        for kw in opp_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", raw_name, re.IGNORECASE):
                score += 50
                match_reasons.append(f"Opponent match '{kw}' (+50 pts)")
                break

        dt_match = re.search(r"(\d{4}-\d\d-\d\d)\s*\|\s*(\d\d:\d\d)", raw_name)
        if dt_match:
            try:
                str_dt_str = f"{dt_match.group(1)} {dt_match.group(2)}+00:00"
                str_kickoff = datetime.fromisoformat(str_dt_str)
                time_diff = abs((str_kickoff - target_fixture["kickoff"]).total_seconds()) / 3600.0
                if time_diff <= 3.0:
                    score += 40
                    match_reasons.append(f"Kickoff time alignment ({time_diff:.1f}h diff) (+40 pts)")
                elif time_diff > 12.0:
                    score -= 50
                    match_reasons.append(f"Date/Time mismatch ({time_diff:.1f}h diff) (-50 pts)")
            except ValueError:
                pass

    raw_lower = raw_name.lower().strip()
    if raw_lower.startswith("live"):
        score += 20
        match_reasons.append("Stream is currently LIVE (+20 pts)")
    elif raw_lower.startswith(("next", "upcoming")):
        score += 10
        match_reasons.append("Stream is upcoming/NEXT (+10 pts)")
    elif raw_lower.startswith(("end", "ended")):
        score -= 80
        match_reasons.append("Stream has ENDED (-80 pts)")

    channel_slot = extract_channel_slot(raw_name)

    return {
        "stream_id": stream_id,
        "raw_name": raw_name,
        "channel_slot": channel_slot,
        "category": cat_info["name"],
        "lang": cat_info["lang"],
        "score": score,
        "reasons": match_reasons,
        "fixture": target_fixture,
    }


def match_stream_for_bayern(stream, cat_info, fixture=None, espn_fixture=None):
    """Evaluates a provider stream for Bayern Munich match criteria."""
    return match_stream_generic(stream, cat_info, BAYERN_PATTERNS, FALSE_POSITIVES, fixture=fixture, espn_fixture=espn_fixture)


def main():
    print("================================================================")
    print(" BAYERN MUNICH PPV MATCH DETECTOR")
    print("================================================================\n")

    # Step 1: Fixture Schedule Query
    print("[1] Querying OpenLigaDB & ESPN for Bayern Munich fixtures...")
    fixtures, fix_errors = fetch_bayern_fixtures()
    if fix_errors:
        for err in fix_errors:
            print(f"  Warning: {err}", file=sys.stderr)

    if not fixtures:
        print("  ERROR: No upcoming Bayern Munich fixtures could be determined!", file=sys.stderr)
        sys.exit(1)

    next_fixture = fixtures[0]
    print(f"  --> Next Upcoming Fixture:")
    print(f"      League:    {next_fixture['league']}")
    print(f"      Match:     {next_fixture['match_name']}")
    print(f"      Opponent:  {next_fixture['opponent']}")
    print(f"      Kickoff:   {next_fixture['kickoff'].strftime('%Y-%m-%d %H:%M UTC')}")
    if len(fixtures) > 1:
        print(f"      Following: {fixtures[1]['match_name']} ({fixtures[1]['kickoff'].strftime('%Y-%m-%d %H:%M UTC')})")
    print()

    # Step 2: Provider Stream Scan
    print("[2] Scanning Live Streams across Target Provider PPV Categories (via CT 105 egress)...")
    all_matches = []
    total_streams_scanned = 0
    scan_errors = []

    for cat_id, cat_info in TARGET_CATEGORIES.items():
        try:
            streams = fetch_provider_ppv_streams(cat_id)
            total_streams_scanned += len(streams)
            print(f"  Category {cat_id} ({cat_info['name']}): scanned {len(streams)} streams.")
            for s in streams:
                matched_f = find_best_matching_fixture(s.get("name", ""), fixtures) or next_fixture
                match_res = match_stream_for_bayern(s, cat_info, fixture=matched_f)
                if match_res:
                    all_matches.append(match_res)
        except ProviderFetchError as exc:
            scan_errors.append(f"Category {cat_id} ({cat_info['name']}): {exc}")
            print(f"  Category {cat_id} ({cat_info['name']}): ERROR - {exc}", file=sys.stderr)

    if scan_errors:
        print(f"\nProvider scan encountered {len(scan_errors)} errors!", file=sys.stderr)
        sys.exit(1)

    print(f"\nScan complete! Examined {total_streams_scanned} total streams across {len(TARGET_CATEGORIES)} categories.")
    print(f"Live Bayern matches found: {len(all_matches)}")

    for m in sorted(all_matches, key=lambda x: x["score"], reverse=True):
        print(f"\n  [MATCH DETECTED - Score: {m['score']}]")
        print(f"   Stream ID: {m['stream_id']}")
        print(f"   Category:  {m['category']} ({m['lang']})")
        print(f"   Raw Name:  {m['raw_name']}")
        print(f"   Reasons:   {', '.join(m['reasons'])}")

    # Step 3: Test Simulation on Synthesized Edge Cases
    print("\n----------------------------------------------------------------")
    print(" RUNNING TEST SUITE ON SYNTHESIZED TEST CASES")
    print("----------------------------------------------------------------")

    mock_fixture = {
        "opponent": "VfB Stuttgart",
        "kickoff": datetime.fromisoformat("2026-08-28 18:30:00+00:00"),
    }

    test_cases = [
        ("Live | Bayern Munich vs. VfB Stuttgart | Bundesliga | 2026-08-28 | 18:30 (GMT) | 8K EXCLUSIVE | US: DAZN PPV 4", 573, True),
        ("Upcoming | VfB Stuttgart vs. FC Bayern Munich | Bundesliga | 2026-08-28 | 18:30 (GMT) | GB: DAZN PPV 1", 575, True),
        ("Live | FC Bayern Women vs. Eintracht Frankfurt | Frauen Bundesliga | US: DAZN PPV 2", 573, False),
        ("End | Bayern Munich II vs. Schweinfurt | Regionalliga", 573, False),
        ("Live | Bayern Basketball vs. Alba Berlin | BBL", 573, False),
        ("Live | Borussia Dortmund vs. RB Leipzig | Bundesliga | US: DAZN PPV 5", 573, False),
        ("Live | Bayern Munich Highlights & Goals | Magazine", 573, False),
    ]

    passed = 0
    for name, cat_id, expected_match in test_cases:
        cat_info = TARGET_CATEGORIES[cat_id]
        res = match_stream_for_bayern({"stream_id": 999999, "name": name}, cat_info, fixture=mock_fixture)
        is_matched = res is not None and res["score"] > 80
        status = "PASS" if is_matched == expected_match else "FAIL"
        if status == "PASS":
            passed += 1
        print(f" [{status}] Stream: '{name[:65]}...'")
        print(f"         Expected Matched: {expected_match} | Actual Matched: {is_matched} (Score: {res['score'] if res else None})")

    print(f"\nTest Suite Result: {passed}/{len(test_cases)} tests passed.")
    if passed != len(test_cases):
        sys.exit(1)


if __name__ == "__main__":
    main()
