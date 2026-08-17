import datetime
import html
import json
import re
import sys
import time
import urllib.request

TARGET_NETWORK_CODE = "ESPN2"
CHANNEL_ID = "scraped.espn2.us"
DISPLAY_NAME = "GO: ESPN2"
DAYS_TO_FETCH = 3
XMLFMT = "%Y%m%d%H%M%S +0000"


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


def get_airings(date_str):
  url = f"https://www.espn.com/watch/schedule/_/type/upcoming/startDate/{date_str}"
  req = urllib.request.Request(
      url,
      headers={
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          )
      },
  )
  try:
    html_doc = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
  except Exception as e:
    print(f"Error fetching schedule for {date_str}: {e}", file=sys.stderr)
    return []

  marker = "window['__espnfitt__']"
  idx = html_doc.find(marker)
  if idx == -1:
    print(f"Could not find JSON state in {url}", file=sys.stderr)
    return []

  eq_idx = html_doc.find("=", idx)
  if eq_idx == -1:
    return []
  start_idx = eq_idx + 1
  while start_idx < len(html_doc) and html_doc[start_idx] in " \t\r\n":
    start_idx += 1
  try:
    data, _ = json.JSONDecoder().raw_decode(html_doc, start_idx)
  except Exception as e:
    print(f"Failed decoding JSON state in {url}: {e}", file=sys.stderr)
    return []

  airings = []

  def find_arngs(obj):
    if isinstance(obj, dict):
      if "arngs" in obj and isinstance(obj["arngs"], list):
        airings.extend(obj["arngs"])
      for v in obj.values():
        find_arngs(v)
    elif isinstance(obj, list):
      for item in obj:
        find_arngs(item)

  find_arngs(data)
  return airings


def main():
  today = datetime.datetime.now(datetime.timezone.utc)
  all_airings = []

  # fetch live (in case current time is missing from upcoming)
  live_url = "https://www.espn.com/watch/schedule/_/type/live"
  req = urllib.request.Request(live_url, headers={"User-Agent": "Mozilla/5.0"})
  try:
    live_doc = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
    marker = "window['__espnfitt__']"
    idx = live_doc.find(marker)
    if idx != -1:
      eq_idx = live_doc.find("=", idx)
      if eq_idx != -1:
        start_idx = eq_idx + 1
        while start_idx < len(live_doc) and live_doc[start_idx] in " \t\r\n":
          start_idx += 1
        data, _ = json.JSONDecoder().raw_decode(live_doc, start_idx)
        live_airings = []

        def find_arngs(obj):
          if isinstance(obj, dict):
            if "arngs" in obj and isinstance(obj["arngs"], list):
              live_airings.extend(obj["arngs"])
            for v in obj.values():
              find_arngs(v)
          elif isinstance(obj, list):
            for item in obj:
              find_arngs(item)

        find_arngs(data)
        all_airings.extend(live_airings)
  except Exception:
    pass

  for i in range(DAYS_TO_FETCH):
    d = today + datetime.timedelta(days=i)
    date_str = d.strftime("%Y%m%d")
    all_airings.extend(get_airings(date_str))

  # filter and parse
  events = []
  seen = set()

  for a in all_airings:
    if not a.get("stme"):
      continue
    # Check networks
    codes = [b.get("code") for b in a.get("bcsts", [])]
    if TARGET_NETWORK_CODE not in codes:
      continue

    uid = a.get("id")
    if uid in seen:
      continue
    seen.add(uid)

    # parse stme (e.g. 2026-07-20T19:00:00Z)
    stme_str = a["stme"]
    stme_str = stme_str.replace("Z", "+0000")
    try:
      dt = datetime.datetime.strptime(stme_str[:19], "%Y-%m-%dT%H:%M:%S")
      start_ts = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
      continue

    title = html.unescape((a.get("nme") or "TBA").strip())
    desc = ""
    if "sctgys" in a and len(a["sctgys"]) > 0:
      desc = html.unescape(a["sctgys"][0].get("name", "").strip())

    events.append({"start": start_ts, "title": title, "desc": desc})

  events.sort(key=lambda x: x["start"])

  if not events:
    print(
        f"Error: 0 programmes found for {TARGET_NETWORK_CODE}", file=sys.stderr
    )
    sys.exit(1)

  def xml_escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

  print('<?xml version="1.0" encoding="UTF-8"?>')
  print("<tv>")
  print(f'<channel id="{xml_escape(CHANNEL_ID)}">')
  print(f"  <display-name>{xml_escape(DISPLAY_NAME)}</display-name>")
  print("</channel>")

  for i in range(len(events)):
    start = events[i]["start"]
    if i + 1 < len(events):
      next_start = events[i + 1]["start"]
      stop = (
          min(next_start, start + 4 * 3600)
          if next_start > start
          else start + 3600
      )
    else:
      stop = start + 3 * 3600

    if stop <= start:
      stop = start + 3600

    print(_prog(CHANNEL_ID, start, stop, events[i]["title"], events[i]["desc"]))

  print("</tv>")


if __name__ == "__main__":
  main()
