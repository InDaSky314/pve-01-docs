#!/usr/bin/env python3
"""Verify every CHANNEL.epg_mapping blob in NextPVR still parses as XML.

An unescaped `&` in a channel name once made ten of these invalid and aborted
EPG ingest for all 997 channels while reporting success. Run after any bulk
rename; a non-zero exit means guide data is about to stop updating.
"""
import sqlite3
import sys
import xml.etree.ElementTree as ET

DB = "/srv/jellyfin-npvr/nextpvr/config/npvr.db3"

conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
checked = bad = 0
for oid, name, mapping in conn.execute(
        "select oid, name, epg_mapping from CHANNEL"):
    if not mapping:
        continue
    checked += 1
    try:
        ET.fromstring(mapping)
    except ET.ParseError as e:
        bad += 1
        print("INVALID  oid=%s  %s  -- %s" % (oid, name, e))

print("%d mappings checked, %d invalid" % (checked, bad))
sys.exit(1 if bad else 0)
