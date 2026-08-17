import datetime
import html
import json
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

CHANNEL_ID = "scraped.hallmarkdrama.us"
DISPLAY_NAME = "Hallmark Drama HD"
XMLFMT = "%Y%m%d%H%M%S +0000"


def xml_escape(s):
  return (
      s.replace("&", "&amp;")
      .replace("<", "&lt;")
      .replace(">", "&gt;")
      .replace('"', "&quot;")
  )


def scrape_tvinsider(slug, display_name, channel_id):
  url = f"https://www.tvinsider.com/network/{slug}/schedule/"
  req = urllib.request.Request(
      url,
      headers={
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          )
      },
  )
  try:
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
  except Exception as e:
    print(f"Error fetching {url}: {e}", file=sys.stderr)
    return ""

  sections = re.split(r'<h2 id="(\d{2}-\d{2}-\d{4})" class="date">', raw)
  if len(sections) < 3:
    print(f"No schedule date sections found for {slug}", file=sys.stderr)
    return ""

  tz_et = ZoneInfo("America/New_York")
  progs = []

  pattern = (
      r'<a[^>]*class="show-upcoming[^"]*"[^>]*>\s*'
      r'<div[^>]*><time>([^<]+)</time>.*?</div>\s*'
      r'<h3>([^<]+)</h3>'
      r"(?:\s*<h4>([^<]*)</h4>)?"
      r"(?:\s*<h5>([^<]*)</h5>)?"
      r"(?:\s*<h6>([^<]*)</h6>)?"
      r"(?:\s*<p>([^<]*)</p>)?"
  )

  for i in range(1, len(sections), 2):
    d_str = sections[i]
    d_html = sections[i + 1]
    try:
      m_month, m_day, m_year = map(int, d_str.split("-"))
    except ValueError:
      continue

    matches = re.findall(pattern, d_html, re.DOTALL)
    for t_str, title_raw, meta_raw, sub_raw, ep_raw, desc_raw in matches:
      t_str = t_str.strip()
      title = html.unescape(title_raw.strip())
      sub = html.unescape(sub_raw.strip()) if sub_raw else ""
      ep = html.unescape(ep_raw.strip()) if ep_raw else ""
      desc = html.unescape(desc_raw.strip()) if desc_raw else ""

      full_title = f"{title}: {sub}" if sub else title
      full_desc = f"{ep} - {desc}" if ep else desc

      t_m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", t_str, re.I)
      if not t_m:
        continue
      hr = int(t_m.group(1))
      mn = int(t_m.group(2))
      ampm = t_m.group(3).upper()
      if ampm == "PM" and hr < 12:
        hr += 12
      elif ampm == "AM" and hr == 12:
        hr = 0

      # US Eastern timezone (EST/EDT)
      dt_local = datetime.datetime(
          m_year, m_month, m_day, hr, mn, tzinfo=tz_et
      )
      start_ts = int(dt_local.timestamp())
      stop_ts = start_ts + 1800

      progs.append((start_ts, stop_ts, full_title, full_desc))

  if not progs:
    print(f"No schedule matches parsed for {slug}", file=sys.stderr)
    return ""

  progs.sort(key=lambda x: x[0])
  xml_progs = []
  for i in range(len(progs)):
    start, stop, title, desc = progs[i]
    if i < len(progs) - 1:
      next_start = progs[i + 1][0]
      if next_start > start:
        stop = min(next_start, start + 4 * 3600)

    p = (
        f'<programme start="{time.strftime(XMLFMT, time.gmtime(start))}"'
        f' stop="{time.strftime(XMLFMT, time.gmtime(stop))}"'
        f' channel="{xml_escape(channel_id)}">\n'
        f"  <title>{xml_escape(title)}</title>\n"
    )
    if desc:
      p += f"  <desc>{xml_escape(desc)}</desc>\n"
    p += "</programme>"
    xml_progs.append(p)

  out = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      "<tv>",
      f'  <channel id="{xml_escape(channel_id)}">',
      f"    <display-name>{xml_escape(display_name)}</display-name>",
      "  </channel>",
  ]
  out.extend(xml_progs)
  out.append("</tv>")
  return "\n".join(out)


def main():
  xml = scrape_tvinsider("hallmark-family", DISPLAY_NAME, CHANNEL_ID)
  if not xml or "<programme" not in xml:
    print(f"Error: 0 programmes found for {CHANNEL_ID}", file=sys.stderr)
    sys.exit(1)
  print(xml)


if __name__ == "__main__":
  main()
