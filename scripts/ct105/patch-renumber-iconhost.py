#!/usr/bin/env python3
"""Make renumber-xepg.py also carry tvg-logo from the playlist into xepg.

Threadfin keeps its own copy of each channel's logo URL and will not refresh it
from the m3u, because `x-update-channel-icon` is False on all 996 entries.
That setting is deliberate -- it is what preserved good artwork after the
provider degraded many logo URLs to the DirecTV GO placeholder -- so it should
stay False.

The consequence is that the icon-host override reaches playlist.m3u and stops
there: 0 of 996 xepg entries pointed at the icon host after the first sync.
CT 112 is unaffected, since NextPVR reads playlist.m3u directly; this gap is
production-only.

So the logo is synced here, alongside the number and group, and only when the
playlist offers a curated icon-host URL. Provider URLs are never written back
over an existing entry -- that would be the degradation this whole exercise
exists to prevent.
"""
import shutil
import sys

SRC = "/srv/media-core/sync/renumber-xepg.py"
BAK = SRC + ".bak-iconhost"
ICON_HOST = "http://192.168.9.11:8100"


def main():
    s = open(SRC).read()
    if "ICON_HOST" in s:
        print("already patched")
        return 0

    old = '''            if chno and name and name.group(1) not in out:
                out[name.group(1)] = (chno.group(1),
                                      group.group(1) if group else "",
                                      line)'''
    assert old in s, "playlist_map anchor missing"
    new = '''            logo = re.search(r'tvg-logo="([^"]+)"', extinf)
            if chno and name and name.group(1) not in out:
                out[name.group(1)] = (chno.group(1),
                                      group.group(1) if group else "",
                                      line,
                                      logo.group(1) if logo else "")'''
    s = s.replace(old, new, 1)

    old2 = '''            chno, group, url = hit
            if (v.get("x-channelID") != chno or v.get("x-group-title") != group
                    or v.get("url") != url):
                v["x-channelID"] = chno
                v["tvg-chno"] = chno
                v["x-group-title"] = group
                v["group-title"] = group
                v["url"] = url
                fixed += 1'''
    assert old2 in s, "write anchor missing"
    new2 = '''            chno, group, url, logo = hit
            # Only ever adopt a curated icon-host URL. Writing a provider URL
            # back would undo artwork that Threadfin is deliberately pinning.
            want_logo = logo if logo.startswith(ICON_HOST) else None
            if (v.get("x-channelID") != chno or v.get("x-group-title") != group
                    or v.get("url") != url
                    or (want_logo and v.get("tvg-logo") != want_logo)):
                v["x-channelID"] = chno
                v["tvg-chno"] = chno
                v["x-group-title"] = group
                v["group-title"] = group
                v["url"] = url
                if want_logo:
                    v["tvg-logo"] = want_logo
                    relogo += 1
                fixed += 1'''
    s = s.replace(old2, new2, 1)

    s = s.replace('''        fixed = missing = 0''', '''        fixed = missing = relogo = 0''', 1)
    s = s.replace('''        print(f"xepg: renumbered/repaired {fixed} channels "
              f"({missing} not in current playlist)")''',
                  '''        print(f"xepg: renumbered/repaired {fixed} channels "
              f"({missing} not in current playlist)")
        if relogo:
            print(f"xepg: {relogo} channels repointed at the icon host")''', 1)

    s = s.replace('''XEPG = "/srv/media-core/threadfin/conf/xepg.json"''',
                  '''XEPG = "/srv/media-core/threadfin/conf/xepg.json"
# Curated artwork served by icon-host.service on the Proxmox host.
ICON_HOST = "http://192.168.9.11:8100"''', 1)

    shutil.copy2(SRC, BAK)
    open(SRC, "w").write(s)
    compile(s, SRC, "exec")
    print(f"patched; backup {BAK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
