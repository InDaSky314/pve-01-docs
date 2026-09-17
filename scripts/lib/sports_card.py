#!/usr/bin/env python3
"""sports_card -- deterministic artwork for sports recordings.

Composes a 2:3 poster (Jellyfin/Wholphin Primary) and a 16:9 thumb from the
ESPN fixture a capture was matched against: the two team logos, the matchup,
date and venue, and a league badge. Replaces the EPG provider's generic
stills and the channel-logo fallback the owner described as "random screen
grabs" (audit 2026-09-17).

Design constraints, all from agy's WS4b review of the first proposal:
  * poster.jpg MUST be 2:3 portrait. Wholphin renders Primary with
    AspectRatio.TALL + ContentScale.FillBounds, so a landscape poster is
    stretched. The wide card goes to thumb.jpg instead.
  * Soccer convention is "Home vs Away", home first; US sports are
    "Away at Home". Neutral sites get "vs" regardless.
  * Team logos can be missing for smaller clubs -> abbreviation badge.
  * Umlauts/diacritics everywhere -> DejaVu Sans, which covers them.
  * Runs on the host (pve-01). CT 105 has neither Pillow nor fonts.

Pure function of its inputs; the only I/O is the logo cache and the two
output files. No network unless a logo is not yet cached.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

LOGO_CACHE = Path("/var/lib/dvr-dashboard/logo-cache")
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LOCAL_TZ = ZoneInfo("Europe/Berlin")

POSTER_SIZE = (1000, 1500)      # 2:3
THUMB_SIZE = (1920, 1080)       # 16:9
BG = (16, 20, 24)
FG = (240, 240, 240)
MUTED = (160, 168, 176)
ACCENT = (200, 30, 40)

SOCCER_PREFIX = "soccer/"
LEAGUE_LABELS = {
    "football/nfl": "NFL",
    "football/college-football": "College Football",
    "baseball/mlb": "MLB",
    "basketball/nba": "NBA",
    "basketball/mens-college-basketball": "College Basketball",
    "hockey/nhl": "NHL",
    "soccer/ger.1": "Bundesliga",
    "soccer/ger.pokal": "DFB-Pokal",
    "soccer/uefa.champions": "Champions League",
    "soccer/uefa.europa": "Europa League",
    "soccer/eng.1": "Premier League",
}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def cached_logo(url: str | None) -> Image.Image | None:
    """Fetch a team logo once, keep it under LOGO_CACHE keyed by URL hash."""
    if not url:
        return None
    LOGO_CACHE.mkdir(parents=True, exist_ok=True)
    p = LOGO_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".png")
    if not p.exists():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "media-core-sports-card/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                p.write_bytes(r.read())
        except Exception:                                      # noqa: BLE001
            return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:                                          # noqa: BLE001
        return None


def _badge(abbrev: str, size: int) -> Image.Image:
    """Fallback for a club with no usable logo: its abbreviation in a disc."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=(48, 56, 64, 255), outline=(90, 100, 110, 255), width=max(2, size // 60))
    f = _font(FONT_BOLD, int(size * 0.34))
    txt = (abbrev or "?")[:4]
    box = d.textbbox((0, 0), txt, font=f)
    d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), txt, font=f, fill=FG)
    return im


def _fit(im: Image.Image, size: int) -> Image.Image:
    im = im.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return canvas


def _team_image(team: dict[str, Any], size: int) -> Image.Image:
    logo = cached_logo(team.get("logo"))
    if logo is None or min(logo.size) < 64:
        return _badge(team.get("abbreviation") or team.get("displayName", "?")[:3].upper(), size)
    return _fit(logo, size)


def _centered(d: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont,
              width: int, fill=FG) -> int:
    box = d.textbbox((0, 0), text, font=font)
    d.text(((width - (box[2] - box[0])) / 2 - box[0], y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def _shrink_to(text: str, font_path: str, start: int, max_w: int, d: ImageDraw.ImageDraw) -> ImageFont.FreeTypeFont:
    size = start
    while size > 18:
        f = _font(font_path, size)
        box = d.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_w:
            return f
        size -= 4
    return _font(font_path, 18)


def order_teams(fixture: dict[str, Any]) -> tuple[dict, dict, str]:
    """(first, second, joiner). Soccer: home first, 'vs'. US: away first, 'at'.
    Neutral site: 'vs' either way."""
    comps = fixture.get("competitors") or []
    home = next((c for c in comps if c.get("homeAway") == "home"), comps[0] if comps else {})
    away = next((c for c in comps if c.get("homeAway") == "away"), comps[1] if len(comps) > 1 else {})
    soccer = str(fixture.get("sport_path", "")).startswith(SOCCER_PREFIX)
    neutral = bool(fixture.get("neutral_site"))
    if soccer:
        return home, away, "vs"
    return away, home, ("vs" if neutral else "at")


def _lines(fixture: dict[str, Any]) -> tuple[str, str, str, str]:
    first, second, joiner = order_teams(fixture)
    league = LEAGUE_LABELS.get(fixture.get("sport_path", ""), fixture.get("league_label") or "")
    when = fixture.get("start")
    if isinstance(when, datetime):
        when_s = when.astimezone(LOCAL_TZ).strftime("%a %d %b %Y · %H:%M")
    else:
        when_s = str(when or "")
    matchup = f"{first.get('displayName', '?')} {joiner} {second.get('displayName', '?')}"
    venue = fixture.get("venue") or ""
    return league, matchup, when_s, venue


def render_poster(fixture: dict[str, Any], out_path: Path) -> Path:
    W, H = POSTER_SIZE
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    league, matchup, when_s, venue = _lines(fixture)
    first, second, joiner = order_teams(fixture)

    # accent bar + league badge
    d.rectangle((0, 0, W, 14), fill=ACCENT)
    y = 56
    if league:
        y = _centered(d, y, league.upper(), _font(FONT_BOLD, 44), W, MUTED) + 30

    # stacked logos with the joiner between
    logo_sz = 460
    x = (W - logo_sz) // 2
    im.paste(_team_image(first, logo_sz), (x, y), _team_image(first, logo_sz))
    y += logo_sz + 18
    y = _centered(d, y, joiner.upper(), _font(FONT_BOLD, 54), W, ACCENT) + 18
    im.paste(_team_image(second, logo_sz), (x, y), _team_image(second, logo_sz))
    y += logo_sz + 44

    # text block, sized to fit
    f_match = _shrink_to(matchup, FONT_BOLD, 52, W - 80, d)
    y = _centered(d, y, matchup, f_match, W) + 22
    y = _centered(d, y, when_s, _font(FONT_REG, 38), W, MUTED) + 14
    if venue:
        f_v = _shrink_to(venue, FONT_REG, 34, W - 80, d)
        _centered(d, y, venue, f_v, W, MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path


def render_thumb(fixture: dict[str, Any], out_path: Path) -> Path:
    W, H = THUMB_SIZE
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    league, matchup, when_s, venue = _lines(fixture)
    first, second, joiner = order_teams(fixture)

    d.rectangle((0, 0, 18, H), fill=ACCENT)
    logo_sz = 560
    top = (H - logo_sz) // 2 - 60
    im.paste(_team_image(first, logo_sz), (200, top), _team_image(first, logo_sz))
    im.paste(_team_image(second, logo_sz), (W - 200 - logo_sz, top), _team_image(second, logo_sz))
    _centered(d, top + logo_sz // 2 - 40, joiner.upper(), _font(FONT_BOLD, 80), W, ACCENT)

    y = top + logo_sz + 40
    if league:
        y = _centered(d, y, league.upper(), _font(FONT_BOLD, 36), W, MUTED) + 16
    f_match = _shrink_to(matchup, FONT_BOLD, 56, W - 160, d)
    y = _centered(d, y, matchup, f_match, W) + 18
    tail = when_s + (f"   ·   {venue}" if venue else "")
    f_t = _shrink_to(tail, FONT_REG, 36, W - 160, d)
    _centered(d, y, tail, f_t, W, MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path


def render_cards(fixture: dict[str, Any], out_dir: Path, basename: str | None = None) -> dict[str, Path]:
    """Write poster + thumb. With `basename`, write per-recording files
    (<basename>-poster.jpg / <basename>-thumb.jpg) so several games sharing a
    folder do not share one poster -- the 'Live NFL Football' folder holds
    three different games behind a single poster.jpg today."""
    out_dir = Path(out_dir)
    stem = f"{basename}-" if basename else ""
    return {
        "poster": render_poster(fixture, out_dir / f"{stem}poster.jpg"),
        "thumb": render_thumb(fixture, out_dir / f"{stem}thumb.jpg"),
    }


def fixture_from_espn_event(event: dict[str, Any], sport_path: str) -> dict[str, Any]:
    """Normalise one ESPN scoreboard/schedule event into the dict this module
    renders. Keeps exactly the fields needed; nothing else."""
    comp = (event.get("competitions") or [{}])[0]
    comps = []
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        comps.append({"displayName": t.get("displayName") or t.get("name") or "?",
                      "abbreviation": t.get("abbreviation") or "",
                      "logo": t.get("logo") or ((t.get("logos") or [{}])[0].get("href")),
                      "homeAway": c.get("homeAway")})
    try:
        start = datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00"))
    except ValueError:
        start = None
    return {"name": event.get("name") or "", "start": start, "sport_path": sport_path,
            "competitors": comps, "venue": (comp.get("venue") or {}).get("fullName") or "",
            "neutral_site": bool(comp.get("neutralSite"))}


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 4:
        print("usage: sports_card.py <espn_event.json> <sport_path> <out_dir> [basename]", file=sys.stderr)
        sys.exit(2)
    ev = json.load(open(sys.argv[1]))
    fx = fixture_from_espn_event(ev, sys.argv[2])
    print(render_cards(fx, Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None))
