#!/usr/bin/env python3
"""Scarica gli autovelox italiani da OpenStreetMap (Overpass, licenza ODbL), con retry su più server."""
import json, time, urllib.request, urllib.parse, collections, pathlib, sys
Q = """[out:json][timeout:180];
area["ISO3166-1"="IT"][admin_level=2]->.it;
( node["highway"="speed_camera"](area.it);
  relation["type"="enforcement"]["enforcement"~"maxspeed|average_speed|mindistance"](area.it); );
out body; >; out skel qt;"""
SERVER = ["https://overpass-api.de/api/interpreter",
          "https://overpass.kumi.systems/api/interpreter",
          "https://overpass.private.coffee/api/interpreter"]
d = None
for tentativo in range(3):
    for url in SERVER:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": Q}).encode(),
                                         headers={"User-Agent": "autovelox-dati/1.0"})
            d = json.load(urllib.request.urlopen(req, timeout=240))
            print("OSM da", url); break
        except Exception as e:
            print("  errore", url, e, file=sys.stderr)
    if d: break
    time.sleep(60)
if not d: sys.exit("Overpass non raggiungibile")
pathlib.Path("osm_velox.json").write_text(json.dumps(d))
cam = [e for e in d["elements"] if e["type"] == "node" and e.get("tags", {}).get("highway") == "speed_camera"]
print(f"nodi speed_camera: {len(cam)}  con direction: {sum('direction' in e.get('tags', {}) for e in cam)}")
