Here is the complete diagnostic report based on Jellyfin 10.11.x server source code analysis (`jellyfin/jellyfin` repository: `TimerInfoDto.cs`, `TimerInfo.cs`, `DefaultLiveTvService.cs`, `RecordingsManager.cs`) and empirical validation against the live system.

---

### Executive Summary: The Real Root Cause

1. **`IsSports` is not a writable DTO field.**
   In Jellyfin's server API, `TimerInfoDto` (and its base class `BaseTimerInfoDto`) **does not possess an `IsSports` or `Genres` property**. When clients send `"IsSports": true` in the `POST /LiveTv/Timers` payload, C#'s JSON deserializer (`System.Text.Json`) silently ignores and discards the field.
2. **`IsSports` on the server is a read-only computed property.**
   In Jellyfin's internal model (`MediaBrowser.Controller.LiveTv.TimerInfo`), `IsSports` is defined as:
   ```csharp
   public bool IsSports => Tags.Contains("Sports", StringComparison.OrdinalIgnoreCase);
   ```
   `IsSports` is `true` if and only if `TimerInfo.Tags` contains the string `"Sports"`.
3. **Tags are populated from the EPG `LiveTvProgram` metadata via `CopyProgramInfoToTimerInfo()`.**
   When `POST /LiveTv/Timers` is called, Jellyfin attempts to link the new timer to an EPG program in one of two ways (`DefaultLiveTvService.cs`):
   - **Method A (Explicit):** Via the `ProgramId` property in the POST body.
   - **Method B (Fuzzy Fallback):** Searching the EPG for a program on `ChannelId` whose `StartDate` falls within **`[Timer.StartDate - 3 min, Timer.StartDate + 3 min]`**.

4. **Why the scripts' timers failed to get categorized:**
   - The scripts **never sent `ProgramId`**.
   - The scripts calculated `StartDate` by **subtracting 5 minutes** from the game's actual start time (`game_start - 5m`).
   - When Jellyfin tried its Method B fallback search using `(game_start - 5m)`, it searched the window `[(game_start - 8m), (game_start - 2m)]`. The real EPG program started at `game_start` (0m offset), which fell **outside** the 6-minute search window!
   - Because both Method A and Method B failed, `programInfo` returned `null`, `CopyProgramInfoToTimerInfo()` was skipped, `TimerInfo.Tags` remained empty `[]`, and `TimerInfo.IsSports` remained `false`.
   - When the recording completed, `RecordingsManager.GetRecordingPath()` checked `timer.IsProgramSeries`, `timer.IsMovie`, `timer.IsKids`, and `timer.IsSports`. Since all were `false`, Jellyfin dropped the recording into the root/default directory (**"Other"**).

---

### Resolution of Tonight's 4 Contradictions

| # | Observation | Server Source Explanation |
|---|---|---|
| **1** | Adding `"IsSports": true` to payload still resulting in `"IsSports": false` in `timers.json` on disk. | `TimerInfoDto` does not have an `IsSports` field, so the POST input field is discarded. Disk serialization writes the backend `TimerInfo` object, whose `IsSports` getter evaluated to `false` because `Tags` was `[]`. |
| **2** | EPG Program item carries `IsSports: true`, but the Timer created for the same game shows `IsSports: false`. | The timer was never linked to the EPG Program item because `ProgramId` was omitted and the script's `-5m` `StartDate` offset broke Jellyfin's 6-minute fallback search window. |
| **3** | `GET /LiveTv/Timers/Defaults?programId=X` returns no `IsSports` or `Genres` fields. | `TimerInfoDto` / `SeriesTimerInfoDto` schemas do not include `IsSports` or `Genres` at all. Live TV program metadata resides on the `LiveTvProgram` object (`dto.ProgramInfo`). |
| **4** | Minimal POST `{"ProgramId":..., "ChannelId":...}` failed with `"Error processing request"`. | `LiveTvDtoService.GetTimerInfo` and `DefaultLiveTvService` require non-null values for required fields (`StartDate`, `EndDate`, `Name`, `ServiceName`: `"Emby"`). Omitting them triggers a C# DTO conversion/validation error. |

---

### Jellyfin Source Code Reference Chain

#### 1. DTO Schema Definition (`MediaBrowser.Model/LiveTv/TimerInfoDto.cs` & `BaseTimerInfoDto.cs`)
```csharp
public class BaseTimerInfoDto : IHasServerId {
    public string Id { get; set; }
    public string ProgramId { get; set; }
    public Guid ChannelId { get; set; }
    public string Name { get; set; }
    public DateTime StartDate { get; set; }
    public DateTime EndDate { get; set; }
    public string ServiceName { get; set; }
    public int PrePaddingSeconds { get; set; }
    public int PostPaddingSeconds { get; set; }
    // NOTE: No IsSports, IsMovie, IsSeries, or Tags fields exist here!
}
```

#### 2. Server Model Computed Getter (`MediaBrowser.Controller/LiveTv/TimerInfo.cs`)
```csharp
public bool IsKids => Tags.Contains("Kids", StringComparison.OrdinalIgnoreCase);
public bool IsSports => Tags.Contains("Sports", StringComparison.OrdinalIgnoreCase);
public bool IsNews => Tags.Contains("News", StringComparison.OrdinalIgnoreCase);
public string[] Tags { get; set; }
```

#### 3. EPG Linking Logic (`src/Jellyfin.LiveTv/DefaultLiveTvService.cs`)
```csharp
LiveTvProgram programInfo = null;

if (!string.IsNullOrWhiteSpace(info.ProgramId))
{
    programInfo = GetProgramInfoFromCache(info.ProgramId); // Method A: Direct ID lookup
}

if (programInfo is null)
{
    // Method B: Fallback search [StartDate - 3m, StartDate + 3m]
    programInfo = GetProgramInfoFromCache(info.ChannelId, info.StartDate);
}

if (programInfo is not null)
{
    CopyProgramInfoToTimerInfo(programInfo, info);
}
```
Inside `CopyProgramInfoToTimerInfo`:
```csharp
timerInfo.Tags = programInfo.Tags; // <--- Copies "Sports" tag into TimerInfo
timerInfo.IsMovie = programInfo.IsMovie;
timerInfo.IsProgramSeries = programInfo.IsSeries;
timerInfo.IsSeries = programInfo.IsSeries;
```

#### 4. Folder Decision Engine (`src/Jellyfin.LiveTv/Recordings/RecordingsManager.cs`)
```csharp
private string GetRecordingPath(TimerInfo timer, RemoteSearchResult? metadata, out string? seriesPath)
{
    var recordingPath = DefaultRecordingPath;
    var config = _config.GetLiveTvConfiguration();

    if (timer.IsProgramSeries) {
        if (config.EnableRecordingSubfolders) recordingPath = Path.Combine(recordingPath, "Series");
        ...
    } else if (timer.IsMovie) {
        if (config.EnableRecordingSubfolders) recordingPath = Path.Combine(recordingPath, "Movies");
        ...
    } else if (timer.IsSports) {
        if (config.EnableRecordingSubfolders) recordingPath = Path.Combine(recordingPath, "Sports");
        recordingPath = Path.Combine(recordingPath, _fileSystem.GetValidFilename(timer.Name).Trim());
    } else {
        // Fallback to root recording folder ("Other")
    }
    return recordingPath;
}
```

---

### Empirical Proof from Live System

Inspecting tonight's live system for the **Brewers @ Dodgers** game (`2026-08-16`):

1. **Jellyfin's EPG Program (`GET /LiveTv/Programs?ChannelIds=5a3d...`)**:
   - `Id`: `"fe01a93b5bc29825795248c5f5190c9e"`
   - `Name`: `"Milwaukee Brewers at Los Angeles Dodgers"`
   - `StartDate`: `"2026-08-16T20:10:00.0000000Z"`
   - `IsSports`: `true`
2. **The Script's Active Timer (`GET /LiveTv/Timers`)**:
   - `Id`: `"b857f82cc9b7c6fbaf5d1dda4dc8f7ea"`
   - `Name`: `"Brewers: MIL @ LAD"`
   - `ProgramId`: `null` *(Missing!)*
   - `StartDate`: `"2026-08-16T20:05:00.0000000Z"` *(Offset by -5 minutes!)*

Because `ProgramId` was `null` and `20:05:00` did not match `20:10:00 +/- 3m`, `programInfo` was `null`, `Tags` remained `[]`, `IsSports` evaluated to `false`, and the resulting recording will land in `Recordings/` ("Other").

---

### Correct API Client Solution

To guarantee that recordings land in the `Sports` subfolder, client scripts must pass the real EPG `ProgramId` when calling `POST /emby/LiveTv/Timers`.

#### 1. Correct Sequence of API Calls
1. **Query EPG Program ID:**
   `GET /LiveTv/Programs?ChannelIds=<channel_id>&HasAired=false`
   Find the program matching the game start time. Extract `program["Id"]`, `program["StartDate"]`, `program["EndDate"]`, and `program["Name"]`.
2. **Post Timer with `ProgramId`:**
   `POST /emby/LiveTv/Timers`

#### 2. Exact POST Body Payload
```json
{
  "Type": "Timer",
  "ProgramId": "fe01a93b5bc29825795248c5f5190c9e",
  "ChannelId": "5a3d2976b8ddd4d9333798246a3d354b",
  "ChannelName": "BSWI / Bally Sports Wisconsin",
  "ServiceName": "Emby",
  "Name": "Milwaukee Brewers at Los Angeles Dodgers",
  "StartDate": "2026-08-16T20:10:00Z",
  "EndDate": "2026-08-16T23:10:00Z",
  "PrePaddingSeconds": 300,
  "PostPaddingSeconds": 1800
}
```
*Note: `StartDate` and `EndDate` should be the program's official EPG times. Padding is handled cleanly via `PrePaddingSeconds` and `PostPaddingSeconds`.*

#### 3. Changes Required in Scripts (`sports-dvr-auto` & `dvr-dashboard`)
In `schedule_game_timer()`:
1. Remove `"IsSports": True` from payload (it has no effect).
2. Fetch upcoming EPG programs for `channel_id` via `jf_request(f"/LiveTv/Programs?ChannelIds={channel_id}&HasAired=false")`.
3. Locate the program starting closest to `game["start"]`.
4. Populate `"ProgramId": epg_program["Id"]` in the payload (falling back to exact `game["start"]` without `-5m` offset if no EPG item exists).
