# Scope (do not build): `epg-find` — search the EPG and book anything

**PLAN ONLY. Change nothing.** Write no files outside your report, edit no scripts, create no
bookings or timers, touch no services, and do NOT open the tuner. An MCT capture of the
Brewers game is scheduled for tonight 01:15 CEST on channel 121 and must not be disturbed.
Deliver a design document, not code — Claude Code reviews it tomorrow before anything is built.

## The goal
Today MCT can only record what the DVR dashboard already knows about: ESPN team schedules
(Brewers/Bucks/Badgers/Packers) and OpenLigaDB (Bayern). The owner wants to record **any**
programme — other sporting events, or arbitrary shows — by searching the guide.

## Ground truth already established today (do not re-derive; verify only if you doubt it)
Two sources exist. **Do not recreate or re-import the EPG.**

1. **Jellyfin `/LiveTv/Programs`.** These filters work: `ChannelIds`, `MinStartDate`,
   `MaxStartDate`, `IsSports` and category flags, `Limit`. Returns exact `StartDate`/`EndDate`.
   **Text search does NOT work** — `Name=`, `SearchTerm=` and `NameStartsWith=` each returned
   the identical 19,113 rows, i.e. the parameter is ignored. `ChannelName` came back null.
2. **XMLTV at `/srv/media-core/epg/epg.xml`** — 25 MB, **24,503 programmes, 1,224 channels**,
   each `<programme start= stop= channel=>` with `<title>` and `<desc>`. Title search lives here.

A title sweep over the XMLTV found e.g. boxing 42, tennis 21, premier league 16, nascar 18,
documentary 48 — with exact start/stop and channel names. So the capability is real; the
question is how to shape it.

## Design the following, with reasoning
* **Interface.** CLI, dashboard UI, or both? What query fields — free text, channel, date,
  time window, category? What does a result row contain so the user can choose confidently?
* **Which source for which query.** Channel+window is cheap and authoritative from Jellyfin;
  title search needs the XMLTV. Where is the seam, and what does a 25 MB parse cost per query?
  Is a cache or index worth it, and if so what invalidates it (the sync regenerates epg.xml)?
* **Duplicate events across channels.** The same fixture appears on several channels — Premier
  League showed on both Chicago NBC 5 and Green Bay NBC 26. That is useful (a working channel
  when a group is broken, as DirecTV Stream is right now) but results must be grouped so the
  user picks deliberately. How?
* **Booking path.** A chosen result must produce an MCT booking with the exact window.
  **It must go through the dashboard's existing `_record_game` arbitration, not write to
  `mct-bookings.json` directly** — bypassing the conflict checks would allow double-booking the
  single tuner, the exact failure the engine-choice design exists to prevent. Show how.
* **Stop time for arbitrary programming.** ESPN/OpenLigaDB give no signal for a random boxing
  card or documentary. EPG `stop` is a plan, not an outcome. Recommend how to treat it — pad,
  cap, or something better — and be explicit about what is guaranteed vs hoped.
* **Failure modes.** Stale EPG, a title matching hundreds of rows, a programme that moves,
  a channel in a broken provider group, ambiguous matches.

## Constraints
Single tuner; Threadfin stays at 1. Never print provider credentials — the URL path carries
them and logs reach Loki. Pinned image tags only. Keep any proposal reversible, and state a
back-out for each piece.

## Deliverable
A scoped design with a recommended option (not a survey), the trade-offs you rejected and why,
an implementation order, and the specific tests that would prove each part in BOTH directions.
Flag anything you think is a bad idea, including in the framing above.
