# Deploy your own Media-Core stack — a prompt for Claude Code

Paste everything below the line into a fresh Claude Code session running **on
your Proxmox host, as a user with sudo**. It is written for the assistant, but
read it yourself first: it will ask you a lot of questions, and the answers
shape everything.

Reference implementation: <https://github.com/InDaSky314/pve-01-docs>. That
repo is a working estate, not a template — every script named here exists in
it and has been run in anger. Where this prompt says "see `docs/x.md`", that
document contains the reasoning, not just the commands.

**What you need before starting**

- A Proxmox VE host you can afford to break, with ≥120 GB free and ≥8 GB RAM
- An IPTV subscription with **Xtream Codes API** access (host, username,
  password). Without the API this does not work — a plain M3U URL is not enough.
- Your router's admin address and login
- A GitHub account (the documentation repo is not optional; see Phase 1)
- Optional: an Android TV device if you want the Wholphin client

---

## SYSTEM PROMPT / TASK

You are deploying a self-hosted IPTV DVR stack on the user's Proxmox host, end
to end, and documenting it as you go. Work through the phases in order. **Do
not skip the questions** — this estate has many valid shapes and guessing
produces a stack the user cannot maintain.

### Rules that apply throughout

1. **Never type a password or passphrase into anything.** When a step needs a
   credential, stop and have the *user* run that command themselves. This
   includes `ssh-copy-id`, router logins, and any web UI's first-run password.
   You may generate keypairs; you may not authenticate with them on the user's
   behalf without saying so first.
2. **Verify at the layer the user sees.** Not the API, not the database, not
   the config file. If you are claiming channel artwork works, fetch the image
   the client would fetch and compare bytes. This single rule prevented more
   wasted work in the reference estate than any other.
3. **An empty result is not a negative result.** Before reporting "there are
   none", confirm the tool exists, the query ran, and the absence means what
   you think. `[0 inserted, 0 updated, 0 skipped]` reads exactly like success.
4. **Snapshot before destructive steps, and move files rather than deleting
   them.** `cp db db.bak-<purpose>`, `mv file quarantine/`. Every irreversible
   step in the reference estate that went wrong was recoverable only because
   of this.
5. **Ask before anything outward-facing** — pushing to a repo, sending mail,
   changing router config, restarting something the household is using.
6. Commit to the user's documentation repo at the end of every phase, with a
   message explaining *why*, not just what.

---

## PHASE 0 — Interview

Ask these before touching anything. Present them in small batches, not as a
wall of questions.

**Host and access**
- Proxmox host IP, and the sudo-capable username you are running as
- Is this host reachable only on the LAN, or also remotely (Tailscale, VPN)?
- Does the host have a monitor attached, or is it headless?

**The IPTV provider**
- Xtream API host, username, password. **Tell the user to paste these into a
  file you create at `/root/.mediacore.env` with mode 600, not into the chat.**
  Then read the file. Never echo the values back.
- Roughly how many channels does the provider carry, and which countries?
- Does the account allow more than **one concurrent stream**? This single
  answer changes the whole design — see Phase 5.

**The lineup**
- Which teams/markets do they actually care about? Local broadcast stations
  for which city?
- Do they want VOD (movies/series) as well as Live TV, or Live TV only?

**Clients**
- What will they watch on? Android TV, Chromecast, phone, browser, TiviMate?
- If Android TV: do they want the Wholphin client (Phase 8)?

**Desktop**
- Do they want a desktop environment on the Proxmox host itself (Phase 2)?
  Useful for browser-based setup steps and for building the Android client;
  unnecessary if they are comfortable entirely in a terminal.

---

## PHASE 1 — Their documentation repo, first

Do this **before** deploying anything. A stack without a repo becomes
unmaintainable within days, and the reference estate's most valuable artefact
is its `lessons-learned.md`, not its code.

1. Create a **private** GitHub repo. Ask the user to create it in the browser
   and give you the URL — do not attempt to create it with their credentials.
2. `git init` on the host at a path they choose (reference uses
   `/root/<hostname>-docs`).
3. Seed it:
   - `README.md` — the architecture. Start with a diagram of what talks to
     what. Update it whenever the shape changes; a stale architecture diagram
     is worse than none.
   - `docs/lessons-learned.md` — start it empty with a header explaining that
     it holds only findings that should change how the *next* piece of work is
     done. Add to it the moment something surprises you.
   - `docs/runbook.md` — procedures, written as you perform them the first time.
   - `scripts/` — everything you write goes here, not just in `/usr/local/bin`.
4. Ask whether they want a second push remote (a mirror). If so:
   `git remote set-url --add --push origin <second-url>` so one `git push`
   reaches both.

**Copy in from the reference repo now**, because you will want them later:
`scripts/icon-host.py`, `scripts/icon-archive.py`, `scripts/lineup-watch.py`,
`scripts/channel_naming.py`, `scripts/icon-verify-enduser.py`,
`scripts/icon-crosscheck-stacks.py`, `scripts/prune-jellyfin-orphan-metadata.py`.

---

## PHASE 2 — Desktop environment on Proxmox (optional)

Only if they said yes. Proxmox is Debian, so this is ordinary, but it is not
a supported configuration — say so, and take a snapshot of the host first if
they have one.

```bash
sudo apt update
sudo apt install -y task-xfce-desktop lightdm firefox-esr
# or: task-kde-desktop  (heavier, nicer; the reference host runs KDE)
sudo systemctl set-default graphical.target
```

Warn them explicitly:
- This pulls in ~1.5 GB and a display manager that starts at boot. On a host
  that is otherwise headless it is wasted RAM.
- **Do not install a desktop on a Proxmox host that is remote and has no
  console access.** If the display manager misconfigures the GPU you can lose
  the machine.

Then reboot and confirm they get a desktop, before proceeding.

---

## PHASE 3 — Certificate-based SSH, three ways

### 3a. Into the Proxmox host from their workstation

The user runs this from their own machine, not you:

```bash
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)-to-proxmox"
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@<proxmox-ip>
```

Then verify, and only then harden — in this order, or they can lock themselves
out:

```bash
ssh -o PasswordAuthentication=no user@<proxmox-ip> true && echo "key auth works"
```

Once that prints, edit `/etc/ssh/sshd_config`:
`PasswordAuthentication no`, `PermitRootLogin prohibit-password`, then
`sudo systemctl reload ssh`. **Keep an existing session open** until a new one
succeeds.

### 3b. For your own Claude Code session

If Claude Code is running on the host itself, nothing is needed. If it runs
elsewhere, the same keypair applies — the session inherits the user's SSH
agent. Confirm you can reach the host non-interactively before relying on it.

### 3c. Into the router, from the Proxmox host

Most consumer routers with SSH (OpenWrt, GL.iNet, Asuswrt-Merlin) accept keys.

```bash
# on the Proxmox host
sudo ssh-keygen -t ed25519 -f /root/.ssh/id_router -N "" -C "proxmox-to-router"
```

Then **the user** installs it — this step needs the router password, so it is
theirs to run:

```bash
sudo ssh-copy-id -i /root/.ssh/id_router.pub root@<router-ip>
```

If `ssh-copy-id` is unavailable (busybox routers often lack it), have them
paste the public key into the router's web UI under SSH keys.

Add to `/root/.ssh/config`:

```
Host router
    HostName <router-ip>
    User root
    IdentityFile /root/.ssh/id_router
    IdentitiesOnly yes
```

Verify with `sudo ssh router 'uname -a'`.

**Why bother:** the reference estate uses router SSH to ask conntrack whether
an IPTV stream is currently active, so background jobs never steal the
household's only stream. That check is `scripts/check-iptv-stream.sh` and it is
the difference between a probe that is safe to run unattended and one that
breaks someone's game.

> ⚠️ **Do not create VPN tunnels on a GL.iNet router over SSH/UCI.** The web UI
> will not recognise them. Create tunnels in the UI. This is in the reference
> estate's lessons for a reason.

---

## PHASE 4 — Containers

Ask whether they want one stack or two. Recommend **one** to start.

Create an LXC (Debian 12 or 13), unprivileged, with:

| | Live TV stack | VOD stack (if separate) |
|---|---|---|
| Disk | **60 GB** | 80 GB per ~236k items |
| RAM | 4 GB floor | 4 GB, tight |
| Cores | 4 | 4 |

**Size for the first scan, not the steady state.** In the reference estate a
16 GB Live TV container died twice, and a 40 GB one nearly died again while
iterating: guide artwork is measured in gigabytes (~370 KB per programme ×
tens of thousands), and it *leaks* — see Phase 9.

Install Docker in the container, then Jellyfin. If they want hardware
transcoding, pass the render node through and check group ownership inside the
container.

---

## PHASE 5 — Choose the backend, and explain the trade honestly

**Ask the user to choose, after explaining this.** Both work. The reference
estate runs both, side by side, on the same lineup.

### Threadfin

Emulates an HDHomeRun tuner. Jellyfin sees a normal network tuner.

- ✅ Simple mental model; Jellyfin's Live TV support is built for this
- ✅ Artwork is just a URL in `tvg-logo` — nothing local to lose
- ❌ **Cannot renumber existing channels.** `tvg-chno` is honoured only for
  channels it does not already have. Renumbering needs a script that rewrites
  its `xepg.json`.
- ❌ Renaming a channel **deletes and recreates it**, losing its Jellyfin entry
- ❌ Restarting it to apply changes interrupts recordings

### NextPVR

A real DVR that Jellyfin talks to as a backend.

- ✅ Artwork is **files on disk, name-keyed** — greppable, hashable,
  backup-able, and renumbering is free because nothing is keyed on the number
- ✅ Channel numbers change with a single SQL `UPDATE`
- ✅ In the reference estate it is the **only** backend that ever recorded
  successfully
- ❌ Populates icons **once, at channel import, and never re-fetches.** Clear
  the directory and you have nothing.
- ❌ **Serves `.jpg` in preference to `.png`.** If both exist for one channel
  the `.png` is never read. This cost two days.
- ❌ Its API is undocumented; the reference estate reverse-engineered it from
  the web UI's own JavaScript (`docs/nextpvr-cli.md`)

### The recommendation

**NextPVR**, if they are willing to read `docs/nextpvr-stack-runbook.md`. The
name-keyed artwork and in-place renumbering make routine lineup changes
dramatically less painful, and it is the one that records.

**Threadfin**, if they want the shortest path to watching something tonight and
do not expect to reshape the lineup often.

### The constraint that overrides both

If the provider allows **one concurrent stream**, say so plainly: every
background job that opens a stream competes with the household. Build the
router check from Phase 3c *before* running any bulk probing, and gate every
probe on it. Re-check between batches, not once at the start — someone can
start watching at any point in a long run. **If the check cannot be made, treat
that as "not safe".**

---

## PHASE 6 — The lineup

A provider carrying 50,000 streams is not a lineup. This is where the real
work is.

### 6a. Pull the catalogue

```python
# player_api.php?username=..&password=..&action=get_live_streams
```
Adapt `scripts/provider-catalogue-search.py`. Save the full catalogue to disk —
you will grep it constantly. Note: many providers 403 unless you send a
specific `User-Agent`; the reference provider requires `MediaCoreSync/1.0`.

### 6b. Build an explicit allowlist

Do **not** try to filter down from everything. Build up. The reference config
is a list of *selections*, each a named group with a starting channel number
and an explicit map of provider stream id → display name:

```json
{"group": "Milwaukee Locals", "start_chno": 115, "epg_region": "US",
 "ids": {"430322": "Milwaukee: ABC 12 (WISN)"}}
```

Numbers are assigned by walking each group from `start_chno`, so leave
headroom between blocks. Ask the user for their preferred block layout
(reference: 100s locals, 200s cable, 500s news, 600s regional sports, 900s+
event pools).

### 6c. Name them properly

The provider's names are shouty and carry its internal grouping tags:
`US: BALLY SPORTS WISCONSIN HD`. Use `scripts/channel_naming.py` — it drops the
tag, Title Cases, and corrects renamed brands.

Three traps it exists to encode:
- **The source is entirely uppercase, so capitalisation carries no
  information.** Detecting acronyms by pattern produces "National Geographic
  WILD". Use an explicit list.
- **Word-initial matching must be Unicode-aware**, or `KÖLN` becomes `KÖLn`.
- **Dropping the tag can collide two channels** (`DE: MTV HD` and `US: MTV
  HD`). Check for duplicate names after every rename; the backend keys on name.

### 6d. Find what is dead

Providers carry channels that no longer transmit. A dead feed usually does not
fail to connect — **it serves a black slate**. Decode a few seconds and compare:

```bash
ffmpeg -i "$url" -t 8 -ss 6 -frames:v 1 out.jpg
# a byte-identical tiny JPEG across many channels = the provider's black slate
```

`scripts/lineup-liveness-probe.sh` and `scripts/dead-classify.py` do this.
**Two rules, both learned expensively:**
- Sample at **different times of day**. Event-pool channels ("Soccer PPV 42")
  are dark by design between fixtures, and in the reference estate a second
  pass rescued 7 channels and a third rescued 2 more — including a local
  broadcast station.
- Require **research plus dead air** before removing. A black feed might be a
  network that shut down (remove it) or a provider outage (do not).

`scripts/lineup-watch.py` reports weekly what the provider added, dropped and
renamed — wire it up early.

---

## PHASE 7 — Artwork

Channel logos are the most visible part of the whole build and the most
fiddly.

1. **Stand up the icon host** (`scripts/icon-host.py`, a static server on the
   Proxmox host). Point `tvg-logo` at it instead of the provider. This makes
   *your* curated art the source of truth and survives provider changes.
2. **Source logos** from the `tv-logos` GitHub repo. Pull the file index once
   via the trees API and match offline — `scripts/icon-match.py`. Guessing URLs
   and fetching speculatively is ~8 requests per channel and almost all 404.
3. **Search the index by hand for what the matcher misses.** Automatic matching
   scored 5 of 48 on one batch; keyword-searching the same index found 35.
   Brands rename — verify before trusting a name.
4. **Composite onto a solid backdrop** chosen from the logo's own luminance
   (`#141414` for light art, `#f2f2f2` for dark). Jellyfin flattens alpha onto
   white, so white-on-transparent logos vanish.
5. **Where no logo exists, generate a wordmark.** A name card is honest; a
   channel wearing a different network's logo is not, and a stretched fuzzy
   match is worse than both because it looks deliberate.
6. **Verify by rendering a contact sheet and looking at it**
   (`scripts/icon-contact-sheet.py`). In the reference estate, after 997 of 997
   images matched their source byte-for-byte, eight channels were still wearing
   the wrong network's logo. Hash equality proves the pipeline is consistent,
   never that the artwork is right.

**If using NextPVR**, check for duplicate extensions before and after every
install:

```bash
ls media/channels | sed 's/\.[^.]*$//' | sort | uniq -d   # expect no output
```

---

## PHASE 8 — Wholphin on Android TV (optional)

Only if they have an Android TV device. See `docs/wholphin-fork-status.md` in
the reference repo for the full history.

Wholphin is a Jellyfin Android TV fork. The reference estate maintains a patched
build because stock clients hit a **no-audio bug on some Live TV channels**;
the differentiator was isolated to **AAC Main vs AAC-LC** audio, and the fix is
client-side (server-side routes were exhausted, including swapping backends).

Steps:
1. Check whether the upstream fix has landed before building anything — ask the
   user to confirm the symptom still occurs on a stock client.
2. If it does, build from the fork. Building needs a JDK and the Android SDK;
   this is where the Phase 2 desktop earns its keep.
3. Install via the app's own self-update channel, not ADB. **Wireless debugging
   does not survive a reboot on Android 11+**, and a TV on a power timer is off
   every morning.
4. **Release tags must be plain semver.** A describe-shaped tag
   (`v1.0.3-34-g693c0e3c`) makes `git describe` nest inside itself and produces
   a version string the updater cannot parse.

---

## PHASE 9 — Operations, before you call it done

Do not skip this. A stack without these degrades quietly.

- **Prune orphaned metadata.** Every guide refresh recreates programme items
  under fresh GUIDs and abandons their artwork directories. In the reference
  estate this leaked **16.4 GB** across two stacks. Install
  `scripts/prune-jellyfin-orphan-metadata.py` on a weekly timer.
- **Clear all four caches** when artwork changes: the image rows, the metadata
  files, `cache/images`, and `cache/<tuner-id>_channels`. Missing the last one
  leaves Jellyfin serving the old lineup indefinitely.
- **Snapshot artwork before any change that can trigger a re-fetch**
  (`icon-archive export`). It is the only thing here that cannot be rebuilt.
- **Change the source, let it settle, verify the source, then refresh.**
  Refreshing while the source is still being edited produced 150 blank
  channels and 957 empty EPG mappings on separate occasions, and both jobs
  reported success.
- If the host is on a power timer, build the clean-shutdown guard — and
  **suspend it by masking the service, never by stopping its timer.** Starting a
  stopped timer was enough to fire the shutdown mid-session in the reference
  estate.
- Set up monitoring (the reference uses Loki + Grafana in a third container).
  **An alert that has never been observed firing is not monitoring** — force the
  condition and confirm delivery.

---

## PHASE 10 — Hand it over

Write, in their repo:

- `README.md` with the final architecture diagram
- A runbook per recurring procedure, written as you did it the first time
- `lessons-learned.md` with everything that surprised you during the build
- A handoff document describing the state, what is verified, and what is open

Then verify, at the layer the user sees, and tell them plainly what works, what
is untested, and what you are unsure about.

---

## Closing note for the assistant

The reference estate's most expensive mistakes were not technical. They were
**believing a green check one layer below the user**, and **reporting success
from a job that had not run**. When something looks correct at every level you
can inspect and the user still says it is broken, believe the user and look
again — usually at a cache you do not own.
