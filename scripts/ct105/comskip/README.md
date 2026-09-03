# comskip deployment set (CT 105)

Deployment artifacts for the commercial-removal pipeline that runs on CT 105:

| file | deployed to |
|---|---|
| `Dockerfile` | builds the comskip image |
| `comskip-postprocess.service` / `.timer` | CT 105 `/etc/systemd/system/` |
| `comskip.ini` | `/srv/media-core/media/recordings/.postprocess/comskip.ini` |
| `on-recording-finished.sh` | `/srv/media-core/media/recordings/.postprocess/` — Jellyfin's hook, which only appends to the queue |

**`process-queue.py` is NOT here — it is [`scripts/process-queue.py`](../../process-queue.py).**

A second copy used to live in this directory and silently went stale: it stopped being
updated after the 2026-08 MKV-mux change and so never received the queue-race fix, the
bitrate sanity ceiling, or the 2026-09-03 sidecar/pass-through changes. It was 298 lines
against the live file's 457, and it defined nothing the maintained copy did not.
`scripts/process-queue.py` is the mirror of `/srv/media-core/comskip/process-queue.py` —
verified byte-identical by md5. Keep it that way; do not re-add a copy here.
