> Filed 2026-08-04 as https://github.com/nk-sys-ops/wholphin/issues/1
>
> Ruled out as a fork regression: the three custom commits touch only
> PlayerFactory.kt and the experimental-settings toggle, and the crash
> stack contains no frames from com.github.damontecres.wholphin.

## Wholphin crashes when a Live TV recording is started (NextPVR backend)

**Build:** `Wholphin Custom 1.0.5-0-gcaf61d30`
**Server:** Jellyfin 10.11.x, Live TV via the **NextPVR** plugin (v13.0.0.0)
**Reproducible:** every time, on any channel

### What happens

Starting a recording from the guide crashes the app immediately. The recording
itself **succeeds** — NextPVR records the programme to completion — so the
failure is purely in handling the notification that follows.

### Root cause

`POST /LiveTv/Timers` returns fine. Jellyfin then pushes a `TimerCreated`
message over the WebSocket whose `Data` object contains **only `ProgramId`**:

```json
{"MessageId":"4b8097427d374e958b0859ba5cf40e3d",
 "Data":{"ProgramId":"c2638dd3fa762dff936659b45581a590"},
 "MessageType":"TimerCreated"}
```

The SDK deserializes `Data` as `org.jellyfin.sdk.model.api.TimerEventInfo`,
where `Id` is **required**, so it throws:

```
kotlinx.serialization.MissingFieldException: Field 'Id' is required for type
with serial name 'org.jellyfin.sdk.model.api.TimerEventInfo', but it was missing
  at org.jellyfin.sdk.api.client.util.ApiSerializer.decodeSocketMessage(ApiSerializer.kt:42)
  at org.jellyfin.sdk.api.sockets.DefaultSocketApi...
  Caused by: ... TimerCreatedMessage$$serializer.deserialize(TimerCreatedMessage.kt:19)
```

ACRA catches it and the app dies.

The same session shows `TimerCancelled` **does** include `Id` and is handled
without incident, which is what isolates this to the created-event payload:

```json
{"Data":{"Id":"946cc9eac78f2c1b71dbf5fc006a6468"},"MessageType":"TimerCancelled"}
```

So the server is emitting a `TimerCreated` event that does not satisfy the
SDK's own contract. The NextPVR plugin's `CreateTimer` path appears not to
propagate the new timer's id.

### Why fix it client-side anyway

Even with the server at fault, one malformed socket message should not take
the app down. A single unparseable event currently kills the process from a
background coroutine.

Suggested: make `Id` nullable on `TimerEventInfo` (or handle it as such
locally), and wrap `decodeSocketMessage` so a deserialization failure logs and
drops the message rather than propagating.

### Scope

This will affect **any** client using the official Jellyfin Kotlin SDK against
a NextPVR backend, not just this fork. Worth reporting upstream to
`jellyfin-sdk-kotlin` and/or `jellyfin-plugin-nextpvr` as well.

### Evidence

Full ACRA report, including the logcat showing the successful POST followed by
the crash 0.15 s later, is on the server at:
`jellyfin/config/log/upload_Wholphin Custom_1.0.5-0-gcaf61d30_20260804191303_*.log`
