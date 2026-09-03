#!/usr/bin/env python3
"""Teach xtream-sync.py to prefer curated artwork from the local icon host.

The provider has degraded many logo URLs to the DirecTV GO placeholder, so
anything that re-fetches artwork loses it. Pointing tvg-logo at our own host
makes the curated art the source of truth for both ecosystems -- and turns a
NextPVR channel re-import from a risk into the delivery mechanism, since
NextPVR populates icons once at import from exactly this field.

Fails open: if the icon host is unreachable the sync emits provider logos
exactly as before. The failure mode is "no change", never "no logos".
"""
import re
import shutil
import sys

SRC = "/srv/media-core/sync/xtream-sync.py"
BAK = SRC + ".bak-iconhost"


def main():
    s = open(SRC).read()

    if "ICON_HOST" in s:
        print("already patched")
        return 0

    # 1. constants + loader, placed after the UA constant
    anchor = 'UA = "MediaCoreSync/1.0"'
    assert anchor in s, "UA anchor missing"
    add = '''UA = "MediaCoreSync/1.0"

# Curated channel artwork, served from the Proxmox host by icon-host.service.
# Preferred over the provider's stream_icon wherever we have art, because the
# provider has replaced many logos with a shared placeholder. Keyed by channel
# name with : / | stripped, matching how the files are written.
ICON_HOST = "http://192.168.9.11:8100"
_ICON_STRIP = re.compile(r"[:/|]")


def icon_overrides():
    """display name -> local icon url. Empty dict if the host is unreachable."""
    try:
        req = urllib.request.Request(f"{ICON_HOST}/index.json",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            cat = json.load(r)
    except Exception as exc:                                  # noqa: BLE001
        print(f"WARNING: icon host unreachable ({exc}) — using provider logos")
        return {}
    return {name: ICON_HOST + path for name, path in cat.items()}


def pick_logo(disp, stream_icon, overrides):
    """Curated art if we have it, else whatever the provider offers."""
    return overrides.get(_ICON_STRIP.sub("", disp).strip()) or stream_icon or ""
'''
    s = s.replace(anchor, add, 1)

    # 2. playlist emission
    old = '''    lines = ["#EXTM3U"]
    for s, group, xid, disp, _region, chno, _mode in chosen:
        logo = s.get("stream_icon") or ""'''
    assert old in s, "playlist anchor missing"
    new = '''    overrides = icon_overrides()
    if overrides:
        print(f"icons: {len(overrides)} curated overrides available")
    lines = ["#EXTM3U"]
    used = 0
    for s, group, xid, disp, _region, chno, _mode in chosen:
        logo = pick_logo(disp, s.get("stream_icon"), overrides)
        if logo.startswith(ICON_HOST):
            used += 1'''
    s = s.replace(old, new, 1)

    old2 = '''    atomic_write(PLAYLIST_OUT, "\\n".join(lines) + "\\n")
    print(f"playlist: {len(chosen)} channels — " +'''
    assert old2 in s, "playlist tail anchor missing"
    new2 = '''    atomic_write(PLAYLIST_OUT, "\\n".join(lines) + "\\n")
    if overrides:
        print(f"icons: {used} channels using curated artwork")
    print(f"playlist: {len(chosen)} channels — " +'''
    s = s.replace(old2, new2, 1)

    # 3. EPG <icon src=...> should agree with the playlist
    old3 = '''            icon = s.get("stream_icon") or ""'''
    assert old3 in s, "epg icon anchor missing"
    new3 = '''            icon = pick_logo(disp, s.get("stream_icon"), _epg_overrides)'''
    s = s.replace(old3, new3, 1)

    old4 = '''        # one <channel> per unique xmltv id, carrying logo + tuner-matching name'''
    assert old4 in s, "epg loop anchor missing"
    new4 = "        _epg_overrides = icon_overrides()\n" + old4
    s = s.replace(old4, new4, 1)

    shutil.copy2(SRC, BAK)
    open(SRC, "w").write(s)
    compile(s, SRC, "exec")      # syntax-check before we walk away
    print(f"patched; backup {BAK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
