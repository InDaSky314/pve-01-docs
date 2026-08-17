import datetime
import html
import json
import sys
import time
import urllib.request

TARGET_TEAM_ID = 109  # Arizona Diamondbacks
CHANNEL_ID = "scraped.bally.arizona"
DISPLAY_NAMES = [
    "Bally Sports Arizona",
    "FanDuel Sports Network Arizona HD",
    "Bally Sports Arizona HD",
]
DAYS_TO_FETCH = 7
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


def main():
  today = datetime.datetime.now(datetime.timezone.utc)
  start_date = today.strftime("%Y-%m-%d")
  end_date = (today + datetime.timedelta(days=DAYS_TO_FETCH)).strftime(
      "%Y-%m-%d"
  )

  url = (
      f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={TARGET_TEAM_ID}&hydrate=broadcasts&startDate={start_date}&endDate={end_date}"
  )
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

  try:
    html_doc = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
  except Exception as e:
    print(f"Error fetching schedule from MLB API: {e}", file=sys.stderr)
    sys.exit(1)

  data = json.loads(html_doc)
  events = []

  for date_obj in data.get("dates", []):
    for game in date_obj.get("games", []):
      away_team = game["teams"]["away"]["team"]["name"]
      home_team = game["teams"]["home"]["team"]["name"]
      title = f"MLB Baseball: {away_team} at {home_team}"
      desc = f"Live coverage of {away_team} at {home_team}."

      stme_str = game["gameDate"]
      try:
        dt = datetime.datetime.strptime(stme_str, "%Y-%m-%dT%H:%M:%SZ")
        start_ts = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
      except Exception:
        continue

      events.append(
          {"start": start_ts, "stop": start_ts + 3 * 3600, "title": title, "desc": desc}
      )

  events.sort(key=lambda x: x["start"])

  primary_name = (
      DISPLAY_NAMES[0] if isinstance(DISPLAY_NAMES, list) else DISPLAY_NAMES
  )
  dn_list = (
      DISPLAY_NAMES if isinstance(DISPLAY_NAMES, list) else [DISPLAY_NAMES]
  )

  if not events:
    # During off-season or long schedule breaks (0 games in window), generate clean daily placeholders
    base_dt = today.replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(DAYS_TO_FETCH):
      day_start = int((base_dt + datetime.timedelta(days=d)).timestamp())
      day_stop = int((base_dt + datetime.timedelta(days=d + 1)).timestamp())
      events.append({
          "start": day_start,
          "stop": day_stop,
          "title": f"{primary_name} Programming",
          "desc": f"No live MLB games scheduled for {primary_name}."
      })

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
  for dn in dn_list:
    print(f"  <display-name>{xml_escape(dn)}</display-name>")
  print("</channel>")

  for i in range(len(events)):
    start = events[i]["start"]
    stop = events[i].get("stop", start + 3 * 3600)
    print(_prog(CHANNEL_ID, start, stop, events[i]["title"], events[i]["desc"]))

  print("</tv>")


if __name__ == "__main__":
  main()
