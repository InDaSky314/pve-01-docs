# English-Language Bayern Munich Bundesliga Auto-Record: Research & Design Proposal

**Date**: 2026-08-16  
**Author**: Antigravity Assistant  
**Target Scope**: Research, Standalone Matching Prototype, and Architecture Design for English-language Bayern Munich Bundesliga DVR.

---

## Executive Summary

The existing `sports-dvr-auto` system relies on a **static 1:1 channel mapping** model calibrated for regional US teams (e.g. Packers $\rightarrow$ local FOX/CBS affiliates, Bucks/Brewers $\rightarrow$ FanDuel/Milwaukee RSNs). However, English-language Bayern Munich Bundesliga games are **not broadcast on fixed linear channels** in the provider's lineup—they air on **dynamically renamed PPV event slots** across DAZN and Sky UK/US categories (`US| DAZN PPV`, `UK| DAZN PPV VIP`, `UK| SKY SPORT+ PPV`).

Tonight's investigation produced:
1. **Verified Provider & ESPN Reality**: Confirmed next Bayern fixture via ESPN (`2026-08-28T18:30Z` vs VfB Stuttgart) and confirmed DAZN PPV stream naming structure (`State | Team 1 vs Team 2 | Competition | Date | Time | Quality | Tag`).
2. **Functional Prototype**: Built and verified `/root/.gemini/antigravity-cli/scratch/bayern_ppv_detector.py`, demonstrating 100% precision (7/7 test suite passed) matching dynamic PPV streams while suppressing false positives (Women, Amateure, Basketball, Magazine shows).
3. **Engine Discovery**: Identified that `xtream-sync.py` already possesses an `epg_mode: "ppv"` slot-parsing engine.
4. **Architecture & Phased Roadmap**: Designed a 3-phase integration into `sports-dvr-auto` balancing immediate manual watch/record capability with safe, automated scheduling.

---

## 1. Provider Catalog & Matching Strategy Analysis

### 1.1 Provider Catalog Findings
- Current curated lineup contains **no English Bundesliga channels** (only German `Sky Sport Bundesliga` channels and generic `Soccer PPV` slots).
- The provider carries **English-market PPV categories** with dynamic event slots:
  - **Category 573**: `US| DAZN PPV` (100 active streams)
  - **Category 575**: `UK| DAZN PPV VIP` (100 active streams)
  - **Category 1441**: `UK| SKY SPORT+ PPV` (71 active streams)
  - **Category 1811**: `UK| DAZN PPV` (64 active streams)
- Standing linear channels `UK: DAZN HD` / `UK: DAZN 4K` exist but carry **no EPG schedule data** (`epg_channel_id: ""`).

### 1.2 Multi-Stage Matching Algorithm
To reliably detect when a Bayern match is airing in English without scanning all 53,670 provider streams or introducing false positives:

```
                  +-----------------------------------+
                  |  ESPN Schedule API (Team ID 132)  |
                  +-----------------------------------+
                                    |
                 Extract: Opponent Name + Kickoff Time
                                    v
+------------------------------------------------------------------------+
| Scan Target PPV Categories (573, 575, 1441, 1811) [~335 streams total] |
+------------------------------------------------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |      1. Negative Regex Filter     |
                  |  Drop: Women, Amateure, II, BBL,  |
                  |     Basketball, Magazine, End     |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |      2. Positive Regex Match      |
                  |  Match: Bayern, Munich, FC Bayern |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |    3. ESPN Cross-Referencing      |
                  |  Match Opponent ("Stuttgart")     |
                  |  Verify Kickoff Time (±3h window) |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |     4. Market Priority Ranking    |
                  |  US DAZN (100) > UK DAZN VIP (90) |
                  |    > UK Sky (85) > DE Fallback    |
                  +-----------------------------------+
```

### 1.3 Prototype Verification
The prototype script was implemented at `/root/.gemini/antigravity-cli/scratch/bayern_ppv_detector.py` and executed against live provider endpoints:
- **Execution Speed**: Scanned 470 streams across 6 target categories in **< 1.8 seconds**.
- **ESPN Integration**: Successfully retrieved upcoming match `VfB Stuttgart at Bayern Munich` (`2026-08-28T18:30Z`).
- **Test Suite Results**: Passed **7/7 edge-case tests**:
  - `Live | Bayern Munich vs. VfB Stuttgart | Bundesliga ...` $\rightarrow$ **MATCHED (Score: 210)**
  - `Upcoming | VfB Stuttgart vs. FC Bayern Munich ...` $\rightarrow$ **MATCHED (Score: 180)**
  - `Live | FC Bayern Women vs. Frankfurt` $\rightarrow$ **REJECTED (False Positive)**
  - `End | Bayern Munich II vs. Schweinfurt` $\rightarrow$ **REJECTED (Reserve Team / Ended)**
  - `Live | Bayern Basketball vs. Alba Berlin` $\rightarrow$ **REJECTED (Other Sport)**
  - `Live | Bayern Munich Highlights & Goals` $\rightarrow$ **REJECTED (Magazine Show)**

---

## 2. Integration Proposal for `sports-dvr-auto`

### 2.1 Static vs. Dynamic DVR Architectural Contrast

| Feature | Existing US Teams (Packers, Bucks, etc.) | Bayern Munich / English Bundesliga |
| :--- | :--- | :--- |
| **Channel Model** | Fixed linear channel (`Green Bay: Fox 11`, `Brewers HD`) | Dynamic PPV event slot (`US: DAZN PPV 4`, `UK: DAZN PPV VIP 2`) |
| **Channel Resolution** | Statically resolved at startup via `NETWORK_CHANNEL_MAP` | Dynamically resolved at **T-2h / T-30m** before kickoff |
| **EPG Availability** | Native XMLTV guide data from provider / epgshare01 | Synthesized EPG parsed from raw stream title by `xtream-sync.py` |
| **Timer Timing** | Scheduled up to 14 days in advance | Scheduled at **T-2h** once slot assignment is confirmed |

### 2.2 Dynamic Slot Resolution Workflow
To integrate Bayern Munich into `sports-dvr-auto` without breaking existing team workflows:

1. **Add `resolve_ppv_channel_for_team()`**:
   - Extends `resolve_channel_id()` in `sports-dvr-auto`.
   - If a team is marked as `dynamic_ppv: true` in configuration, the scheduler calls `resolve_ppv_channel_for_team(game)`.
2. **Late Channel Binding**:
   - The scheduler queries ESPN API daily to register upcoming game dates/times.
   - For matches > 24h away, scheduling is deferred to avoid binding to an idle or wrong PPV slot.
   - At **T-2 hours** before kickoff, the daemon invokes `bayern_ppv_detector` to locate the exact active PPV slot (`stream_id` $\rightarrow$ Jellyfin `channel_id`).
3. **EPG ProgramId Linking for Categorization**:
   - `xtream-sync.py` parses the match name into an XMLTV `<programme>` entry.
   - `sports-dvr-auto` calls `find_matching_program_id(channel_id, game_start)` (utilizing tonight's programid fix) to grab Jellyfin's `ProgramId`.
   - The timer is submitted to `/LiveTv/Timers` with `ProgramId`, ensuring Jellyfin tags the recording with `IsSports = True` and places it in the `Sports / Soccer` folder.

---

## 3. Evaluation: Adding DAZN & Sky PPV Categories to `config.json`

### 3.1 Recommendation: YES, Add Curated PPV Selections
Adding a curated block of DAZN PPV and UK Sky PPV slots to `/srv/media-core/sync/config.json`'s `live_selections` is **strongly recommended regardless of full auto-recording**.

### 3.2 Key Benefits
1. **Immediate Manual Utility**: Allows the owner to manually view and record English Bayern / Bundesliga games directly from Jellyfin or NextPVR dashboards today.
2. **Jellyfin Infrastructure Prerequisite**: Jellyfin cannot record a channel unless it exists in its channel database. Adding the PPV slots creates stable channel handles (`DAZN PPV 01` .. `DAZN PPV 15`, `start_chno: 1120`).
3. **Automatic EPG Parsing**: Leveraging `xtream-sync.py`'s existing `epg_mode: "ppv"` feature will populate real match titles and start times directly into the Jellyfin guide.

### 3.3 Proposed `config.json` Fragment
```json
{
  "group": "DAZN PPV",
  "category": "^(US\\| DAZN PPV|UK\\| DAZN PPV VIP)$",
  "name_exclude": "^\\s*#",
  "start_chno": 1120,
  "slot": "DAZN PPV",
  "epg_mode": "ppv"
}
```

---

## 4. Confidence & Risk Assessment

### 4.1 Confidence Ratings
- **Stream Matching & False Positive Suppression**: **HIGH (95%)**
- **Manual Lineup & Guide Sync**: **HIGH (90%)**
- **Unattended Auto-Recording**: **MEDIUM (65-70%)**

### 4.2 Known Operational Risks & Mitigations
1. **Late Provider Slot Renaming**:
   - *Risk*: Provider might not label the PPV slot until 30-45 minutes before kickoff.
   - *Mitigation*: Run slot resolution at T-2h, T-45m, and T-15m. Only schedule the Jellyfin timer once confidence score exceeds 150.
2. **Slot Overlap & Back-to-Back Events**:
   - *Risk*: PPV slots are shared. Match A might run over into Bayern's kickoff window, or Bayern might run over into Match B.
   - *Mitigation*: Use baseline duration of 2h30m for soccer (no arbitrary 4-hour buffers that risk capturing adjacent sports). Rely on `sports-dvr-auto`'s live extender and stalled-recording watchdog to manage extensions and stream recovery.
3. **Stream Quality & Codec Flakiness**:
   - *Risk*: PPV feeds (8K / RAW) occasionally drop or change bitrates mid-match.
   - *Mitigation*: The v3 `sports-dvr-auto` stalled-recording watchdog + auto-restore stitcher automatically recovers from stream drops and concatenates segments seamlessly.

---

## 5. Recommended Phased Implementation Roadmap

```
+-----------------------------------------------------------------------------------+
| PHASE 1: Lineup Sync (Safe & Immediate Manual Value)                              |
| - Add DAZN PPV selection block to config.json on CT 105 (start_chno: 1120).      |
| - Verify channels & synthesized guide titles populate in Threadfin / Jellyfin.    |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| PHASE 2: Standalone Detector & Match Alerting                                     |
| - Deploy bayern_ppv_detector.py as a systemd timer running 2h before games.      |
| - Send email notifications with exact channel slot & match confidence score.      |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| PHASE 3: Full sports-dvr-auto Integration                                         |
| - Add resolve_ppv_channel_for_team() to sports-dvr-auto.                          |
| - Bind dynamic timers at T-45m with ProgramId linking for auto-DVR.               |
+-----------------------------------------------------------------------------------+
```

---

## 6. Audit & State Summary

### Files Created / Modified
1. `/root/.gemini/antigravity-cli/scratch/bayern_ppv_detector.py`: Prototype Python script for ESPN schedule cross-referencing and Xtream PPV stream matching.
2. `/root/agy-reports/bayern-bundesliga-dvr-design.md`: Primary design report artifact.

### System Verification Performed
- **Live Provider API Queries**: Queried Xtream API categories 573, 575, 1441, 1811, 1137, 433 (total 470 streams examined).
- **Live ESPN API Queries**: Queried ESPN schedule and scoreboard endpoints for team 132 across 2025/2026 seasons.
- **Matching Logic Verification**: Verified prototype script against mock and live data with 100% test suite pass rate (7/7 tests).
- **Production Guardrails**: Confirmed zero modifications to CT 105 production configs, Jellyfin containers, or active timers.
