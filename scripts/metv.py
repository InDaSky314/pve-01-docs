import datetime
import html
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

CHANNEL_ID = "scraped.metv.us"
DISPLAY_NAME = "MeTV"
DAYS_TO_FETCH = 3
XMLFMT = "%Y%m%d%H%M%S +0000"
TZ_ET = ZoneInfo("America/New_York")


def _prog(xid, start, stop, title, desc=""):

  def xml_escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

  p = (
      f'<programme start="{time.strftime(XMLFMT, time.gmtime(start))}" '
      f'stop="{time.strftime(XMLFMT, time.gmtime(stop))}" '
      f'channel="{xml_escape(xid)}">\n'
      f"  <title>{xml_escape(title)}</title>\n"
  )
  if desc:
    p += f"  <desc>{xml_escape(desc)}</desc>\n"
  p += "</programme>"
  return p


def get_airings(base_date):
  date_str = base_date.strftime("%Y-%m-%d")
  url = f"https://www.metv.com/schedule/{date_str}"
  req = urllib.request.Request(
      url,
      headers={
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
              " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
          )
      },
  )
  try:
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
  except Exception as e:
    print(f"Error fetching schedule for {date_str}: {e}", file=sys.stderr)
    return []

  blocks = re.findall(
      r'<div class="schedule-entry-desc">(.*?)<!-- .schedule-entry-desc -->',
      raw,
      re.DOTALL,
  )
  if not blocks:
    blocks = re.findall(
        r'<div class="schedule-entry-desc">(.*?)(?=<div'
        r' class="schedule-entry-desc"|</div>\s*</div>\s*<div id="footer")',
        raw,
        re.DOTALL,
    )

  events = []
  seen_pm = False

  for b in blocks:
    time_m = re.search(
        r"(\d{1,2}:\d{2})\s*<span[^>]*>(am|pm)</span>", b, re.IGNORECASE
    )
    title_m = re.search(
        r'<div class="content-now-title-schedule">\s*<a[^>]*>\s*(.*?)\s*</a>',
        b,
        re.DOTALL,
    )
    if not title_m:
      title_m = re.search(
          r'<div class="content-now-title-schedule">\s*(.*?)\s*</div>',
          b,
          re.DOTALL,
      )

    desc_m = re.search(
        r'<div class="schedule-entry-episode-desc">\s*(.*?)\s*</div>',
        b,
        re.DOTALL,
    )
    ep_title_m = re.search(
        r'<div class="schedule-entry-episode-title">\s*(.*?)\s*</div>',
        b,
        re.DOTALL,
    )

    if time_m and title_m:
      t_str = time_m.group(1) + " " + time_m.group(2).upper()
      ampm = time_m.group(2).upper()
      if ampm == "PM":
        seen_pm = True

      title_str = html.unescape(
          re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
      )

      desc_str = ""
      if ep_title_m:
        desc_str += html.unescape(
            re.sub(r"<[^>]+>", "", ep_title_m.group(1)).strip()
        )
      if desc_m:
        d = html.unescape(re.sub(r"<[^>]+>", "", desc_m.group(1)).strip())
        if desc_str and d:
          desc_str += " - " + d
        elif d:
          desc_str = d

      # MeTV broadcast day runs 5am to 5:30am next day; post-PM AM shows belong to next calendar day
      item_date = (
          base_date + datetime.timedelta(days=1)
          if (seen_pm and ampm == "AM")
          else base_date
      )
      dt_str = f"{item_date.strftime('%Y-%m-%d')} {t_str}"
      try:
        dt_naive = datetime.datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
        dt_aware = dt_naive.replace(tzinfo=TZ_ET)
        start_ts = int(dt_aware.timestamp())

        events.append(
            {"start": start_ts, "title": title_str, "desc": desc_str}
        )
      except Exception:
        continue

  return events


def main():
  now_et = datetime.datetime.now(TZ_ET).date()
  all_airings = []

  for i in range(DAYS_TO_FETCH):
    d = now_et + datetime.timedelta(days=i)
    airings = get_airings(d)
    all_airings.extend(airings)

  # Deduplicate by start timestamp across day boundaries
  seen_starts = set()
  deduped = []
  for a in all_airings:
    if a["start"] not in seen_starts:
      seen_starts.add(a["start"])
      deduped.append(a)

  deduped.sort(key=lambda x: x["start"])

  if not deduped:
    print(f"Error: 0 programmes found for {CHANNEL_ID}", file=sys.stderr)
    sys.exit(1)

  print('<?xml version="1.0" encoding="UTF-8"?>')
  print("<tv>")
  print(f'<channel id="{CHANNEL_ID}">')
  print(f"  <display-name>{DISPLAY_NAME}</display-name>")
  print("</channel>")

  for i in range(len(deduped)):
    start = deduped[i]["start"]
    if i + 1 < len(deduped):
      next_start = deduped[i + 1]["start"]
      stop = (
          min(next_start, start + 4 * 3600)
          if next_start > start
          else start + 1800
      )
    else:
      stop = start + 1800

    print(
        _prog(
            CHANNEL_ID,
            start,
            stop,
            deduped[i]["title"],
            deduped[i]["desc"],
        )
    )

  print("</tv>")


if __name__ == "__main__":
  main()
