## `TimerEventInfo.id` is generated as required, but the API spec says it is optional

### Summary

`TimerEventInfo` is generated with `id: String` — non-nullable, no default —
so `kotlinx.serialization` treats it as mandatory. The Jellyfin API spec does
not list `Id` as required, so a server may legitimately omit it. When it does,
decoding throws and the exception escapes from the socket coroutine.

### The spec

From a Jellyfin 10.11.x `/api-docs/openapi.json`:

```
components.schemas.TimerEventInfo
  required:  (absent)
  properties:
    Id         type: string     (no nullable flag)
    ProgramId  type: string     nullable: true
```

No `required` array, so both properties are optional.

### The generated model

`jellyfin-model/src/commonMain/kotlin-generated/org/jellyfin/sdk/model/api/TimerEventInfo.kt`

```kotlin
@Serializable
public data class TimerEventInfo(
	@SerialName("Id")
	public val id: String,
	@SerialName("ProgramId")
	public val programId: UUID? = null,
)
```

`programId` is optional because the spec marks it `nullable: true`. `id` has no
`nullable` flag and became mandatory. That suggests optionality is being
derived from `nullable` rather than from the schema's `required` array —
which would affect every property in that shape, not just this one.

### Observed failure

Jellyfin with the NextPVR plugin emits, over the WebSocket:

```json
{"MessageId":"4b8097427d374e958b0859ba5cf40e3d",
 "Data":{"ProgramId":"c2638dd3fa762dff936659b45581a590"},
 "MessageType":"TimerCreated"}
```

which is spec-valid, and produces:

```
kotlinx.serialization.MissingFieldException: Field 'Id' is required for type
with serial name 'org.jellyfin.sdk.model.api.TimerEventInfo', but it was missing
  at org.jellyfin.sdk.api.client.util.ApiSerializer.decodeSocketMessage(ApiSerializer.kt:42)
  at org.jellyfin.sdk.api.sockets.DefaultSocketApi$special$$inlined$map$2$2.emit(Emitters.kt:51)
  Caused by: ... TimerCreatedMessage$$serializer.deserialize(TimerCreatedMessage.kt:19)
```

`TimerCancelled` does include `Id` and decodes fine, which is what isolates it
to the created-event payload rather than the socket layer.

On Android this is fatal: the throw happens on `Dispatchers.IO` inside
`DefaultSocketApi`, so it reaches the default handler and terminates the app.
Reproduced on a Jellyfin Android TV client (Wholphin) every time a Live TV
recording is started against a NextPVR backend.

### Suggested fix

`TimerEventInfo.id` should be `String? = null`, and more generally the
generator should treat a property as optional when it is absent from
`required`, independent of `nullable`.

Secondary, if it is in scope for this repo: a decode failure in
`decodeSocketMessage` arguably should not propagate out of the socket flow —
one unexpected message currently kills the consuming application.

### Environment

- jellyfin-sdk-kotlin: as bundled in Jellyfin Android TV / Wholphin 1.0.5
- Jellyfin server 10.11.x, Live TV via jellyfin-plugin-nextpvr 13.0.0.0
