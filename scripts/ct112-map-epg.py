#!/usr/bin/env python3
"""Map NextPVR channels to XMLTV ids by display name.

NextPVR's scan.save does not populate epg_source/epg_mapping when the scan is
driven over HTTP -- it left all 957 empty, and the EPG update then reported
"[0 inserted, 0 updated, 0 skipped]", which is the tell that the mechanism
never ran rather than that there was nothing to do.

Writes the same blob the working database had:

    <epg><source>XMLTV</source><file>/config/epg.xml</file>
         <mapping_id>..</mapping_id><mapping_name>..</mapping_name></epg>

mapping_name is XML-escaped. An unescaped '&' in a channel name ("A&E HD")
once made ten of these invalid and aborted EPG ingest for all 997 channels
while reporting success.

NextPVR must be stopped: it holds the database open and writes on shutdown.
"""
import sqlite3, sys, xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

DB = "/srv/jellyfin-npvr/nextpvr/config/npvr.db3"
EPG = "/srv/jellyfin-npvr/nextpvr/config/epg.xml"
BLOB = ("<epg>\r\n  <source>XMLTV</source>\r\n  <file>/config/epg.xml</file>\r\n"
        "  <mapping_id>{i}</mapping_id>\r\n  <mapping_name>{n}</mapping_name>\r\n</epg>\r\n")

root = ET.parse(EPG).getroot()
by_name = {}
for ch in root.findall("channel"):
    dn = ch.findtext("display-name")
    if dn and dn not in by_name:
        by_name[dn] = ch.get("id")
print("xmltv channels:", len(by_name))

conn = sqlite3.connect(DB, timeout=30)
rows = list(conn.execute("select oid, name from CHANNEL"))
hit, miss = [], []
for oid, name in rows:
    xid = by_name.get(name)
    (hit if xid else miss).append((oid, name, xid))
print("channels:", len(rows), " mapped:", len(hit), " unmatched:", len(miss))
for m in miss[:8]:
    print("   no xmltv entry for", m[1])

if "--apply" not in sys.argv:
    print("\n(dry run — pass --apply)")
    sys.exit(0)

for oid, name, xid in hit:
    conn.execute("update CHANNEL set epg_source=?, epg_mapping=? where oid=?",
                 ("XMLTV", BLOB.format(i=escape(xid), n=escape(name)), oid))
conn.commit()

# every blob must parse, or the next EPG update aborts for everything
bad = 0
for oid, m in conn.execute("select oid, epg_mapping from CHANNEL where epg_mapping != ''"):
    try:
        ET.fromstring(m)
    except ET.ParseError:
        bad += 1
print("wrote %d mappings; invalid XML blobs: %d" % (len(hit), bad))
sys.exit(1 if bad else 0)
