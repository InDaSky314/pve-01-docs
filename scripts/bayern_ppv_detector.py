#!/usr/bin/env python3
"""
Bayern Munich English PPV Match Detector & Scheduler Prototype.

Queries:
1. ESPN Public Soccer API for Bayern Munich (Team ID 132) schedule across Bundesliga, DFB-Pokal, and Champions League.
2. Xtream Codes API for target English PPV categories:
   - Category 573: US| DAZN PPV
   - Category 575: UK| DAZN PPV VIP
   - Category 1441: UK| SKY SPORT+ PPV
   - Category 1811: UK| DAZN PPV
   (and optional DE categories as fallback)

Matching Logic:
- Multi-tier regex matching for Bayern Munich team names.
- Negative regex filtering to eliminate false positives (Women, Amateure, Basketball, Highlights, Replays).
- Cross-referencing opponent name and kickoff timestamp from ESPN schedule.
- Category market prioritization (US English > UK English > DE German).
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

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
    r"\b(women|frauen|II|amateure|u19|u17|basketball|bbl|highlights|replay|magazine)\b",
    re.IGNORECASE
)

# ESPN Soccer Leagues to check for Bayern Munich
ESPN_LEAGUES = [
    ("ger.1", "German Bundesliga"),
    ("ger.pokal", "DFB-Pokal"),
    ("uefa.champions", "UEFA Champions League"),
]

USER_AGENT = "curl/7.88.1"


def fetch_espn_bayern_schedule():
    """Fetch next upcoming Bayern Munich match details from ESPN API."""
    fixtures = []
    now = datetime.now(timezone.utc)
    
    for league_code, league_name in ESPN_LEAGUES:
        # Check current/upcoming seasons
        for season in [2025, 2026]:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams/132/schedule?season={season}"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.load(r)
                for ev in data.get("events", []):
                    dt_str = ev.get("date")
                    if not dt_str:
                        continue
                    try:
                        kickoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    
                    # Only consider future matches or matches started within last 3 hours
                    if kickoff < now - timedelta(hours=3):
                        continue
                    
                    name = ev.get("name", "")
                    short_name = ev.get("shortName", "")
                    comps = ev.get("competitions", [{}])[0]
                    competitors = comps.get("competitors", [])
                    
                    opponent = "Unknown"
                    is_home = True
                    for team_info in competitors:
                        tname = team_info.get("team", {}).get("displayName", "")
                        if "Bayern" not in tname:
                            opponent = tname
                        else:
                            is_home = team_info.get("homeAway") == "home"
                            
                    fixtures.append({
                        "id": ev.get("id"),
                        "league": league_name,
                        "match_name": name,
                        "short_name": short_name,
                        "kickoff": kickoff,
                        "opponent": opponent,
                        "is_home": is_home,
                    })
            except Exception as e:
                pass
                
    # Also check scoreboard endpoint (often has near-term live matches)
    url_sb = "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard"
    try:
        req = urllib.request.Request(url_sb, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            sb_data = json.load(r)
        for ev in sb_data.get("events", []):
            name = ev.get("name", "")
            if "Bayern" in name:
                dt_str = ev.get("date")
                kickoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if kickoff >= now - timedelta(hours=3):
                    comps = ev.get("competitions", [{}])[0]
                    competitors = comps.get("competitors", [])
                    opponent = "Unknown"
                    for team_info in competitors:
                        tname = team_info.get("team", {}).get("displayName", "")
                        if "Bayern" not in tname:
                            opponent = tname
                    fixtures.append({
                        "id": ev.get("id"),
                        "league": "German Bundesliga",
                        "match_name": name,
                        "short_name": ev.get("shortName", ""),
                        "kickoff": kickoff,
                        "opponent": opponent,
                        "is_home": True,
                    })
    except Exception:
        pass

    # Deduplicate by event ID and sort by kickoff
    unique_fixtures = {}
    for f in fixtures:
        unique_fixtures[f["id"]] = f
    sorted_fixtures = sorted(unique_fixtures.values(), key=lambda x: x["kickoff"])
    return sorted_fixtures


def fetch_provider_ppv_streams(cat_id):
    """Fetch live streams for a specific provider category."""
    url = f"{XTREAM_BASE}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams&category_id={cat_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "MediaCoreSync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as exc:
        print(f"Error fetching category {cat_id}: {exc}")
        return []


def extract_channel_slot(raw_name: str) -> str:
    """Extract channel slot label from stream title (e.g. 'US: DAZN PPV 36' or 'GB: DAZN PPV 34')."""
    m = re.search(r"\|\s*([A-Z]{2}:\s*[A-Z0-9\s+]+)$", raw_name, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"\b([A-Z]{2}:\s*[A-Z0-9\s+]+)$", raw_name, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    return raw_name.strip()


def match_stream_generic(stream, cat_info, team_patterns, false_positives_pattern=None, espn_fixture=None):
    """
    Generic evaluator for provider stream match criteria.
    Returns match dictionary if matched, else None.
    """
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
    
    if espn_fixture:
        opp = espn_fixture.get("opponent", "")
        opp_keywords = [w for w in re.findall(r"\w+", opp) if len(w) > 3 and w.lower() not in ["fc", "vfb", "tsv", "1.fc", "sv", "sc"]]
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
                time_diff = abs((str_kickoff - espn_fixture["kickoff"]).total_seconds()) / 3600.0
                if time_diff <= 3.0:
                    score += 40
                    match_reasons.append(f"Kickoff time alignment ({time_diff:.1f}h diff) (+40 pts)")
                elif time_diff > 12.0:
                    score -= 50
                    match_reasons.append(f"Date/Time mismatch ({time_diff:.1f}h diff) (-50 pts)")
            except ValueError:
                pass
                
    if raw_name.startswith("Live"):
        score += 20
        match_reasons.append("Stream is currently LIVE (+20 pts)")
    elif raw_name.startswith("Next"):
        score += 10
        match_reasons.append("Stream is upcoming/NEXT (+10 pts)")
    elif raw_name.startswith("End"):
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
    }


def match_stream_for_bayern(stream, cat_info, espn_fixture=None):
    """Evaluates a provider stream for Bayern Munich match criteria."""
    return match_stream_generic(stream, cat_info, BAYERN_PATTERNS, FALSE_POSITIVES, espn_fixture)



def main():
    print("================================================================")
    print(" BAYERN MUNICH PPV MATCH DETECTOR PROTOTYPE")
    print("================================================ failure/test\n")
    
    # Step 1: ESPN Schedule Query
    print("[1] Querying ESPN Schedule API for Bayern Munich...")
    fixtures = fetch_espn_bayern_schedule()
    next_fixture = fixtures[0] if fixtures else None
    
    if next_fixture:
        print(f"  --> Next Fixture Found:")
        print(f"      League:    {next_fixture['league']}")
        print(f"      Match:     {next_fixture['match_name']}")
        print(f"      Opponent:  {next_fixture['opponent']}")
        print(f"      Kickoff:   {next_fixture['kickoff'].strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        print("  --> No upcoming fixture returned by ESPN API for immediate window.")
    print()

    # Step 2: Provider Stream Scan across Target Categories
    print("[2] Scanning Live Streams across Target Provider PPV Categories...")
    all_matches = []
    total_streams_scanned = 0
    
    for cat_id, cat_info in TARGET_CATEGORIES.items():
        streams = fetch_provider_ppv_streams(cat_id)
        total_streams_scanned += len(streams)
        print(f"  Category {cat_id} ({cat_info['name']}): scanned {len(streams)} streams.")
        
        for s in streams:
            match_res = match_stream_for_bayern(s, cat_info, next_fixture)
            if match_res:
                all_matches.append(match_res)
                
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
    
    mock_espn = {
        "opponent": "VfB Stuttgart",
        "kickoff": datetime.fromisoformat("2026-08-28 18:30:00+00:00")
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
        res = match_stream_for_bayern({"stream_id": 999999, "name": name}, cat_info, mock_espn)
        is_matched = res is not None and res["score"] > 80
        status = "PASS" if is_matched == expected_match else "FAIL"
        if status == "PASS":
            passed += 1
        print(f" [{status}] Stream: '{name[:65]}...'")
        print(f"         Expected Matched: {expected_match} | Actual Matched: {is_matched} (Score: {res['score'] if res else None})")
        
    print(f"\nTest Suite Result: {passed}/{len(test_cases)} tests passed.")


if __name__ == "__main__":
    main()
