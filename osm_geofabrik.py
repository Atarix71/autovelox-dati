#!/usr/bin/env python3
"""Dati OSM senza Overpass: estratto Italia di Geofabrik + osmium-tool.
Produce osm_velox.json (stesso formato di prima: nodi speed_camera + relazioni enforcement con membri)
e strade.json (asse stradale per ogni autovelox, calcolato da strade.calcola)."""
import json, os, re, subprocess, sys, time, urllib.request, pathlib

PBF_URL = "https://download.geofabrik.de/europe/italy-latest.osm.pbf"
PBF = pathlib.Path(os.environ.get("ITALY_PBF", "italy-latest.osm.pbf"))
CLASSI = ("motorway,trunk,primary,secondary,tertiary,unclassified,residential,living_street,service,road,"
          "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link")
BOX_LAT, BOX_LON = 0.0004, 0.0006   # ~45 m attorno a ogni autovelox

def sh(*cmd):
    t = time.time()
    subprocess.run(cmd, check=True)
    print(f"  {cmd[0]} {cmd[1]}: {time.time() - t:.0f} s", flush=True)

def scarica_pbf():
    if PBF.exists() and time.time() - PBF.stat().st_mtime < 20 * 3600:
        print("  estratto Geofabrik già presente:", PBF, f"{PBF.stat().st_size >> 20} MB"); return
    print("  scarico", PBF_URL, flush=True)
    t = time.time(); tmp = PBF.with_suffix(".part")
    req = urllib.request.Request(PBF_URL, headers={"User-Agent": "autovelox-dati/1.0 (github.com/Atarix71/autovelox-dati)"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 22)
            if not b: break
            f.write(b)
    tmp.rename(PBF)
    print(f"  scaricati {PBF.stat().st_size >> 20} MB in {time.time() - t:.0f} s", flush=True)

_ESC = re.compile(r"%([0-9a-fA-F]+)%")
def _unesc(s): return _ESC.sub(lambda m: chr(int(m.group(1), 16)), s)

def leggi_opl(path):
    """Parser OPL minimale: nodi (x,y,tag) e relazioni (tag, membri)."""
    el = []
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split(" ")
        if not f or not f[0]: continue
        kind, oid = f[0][0], int(f[0][1:])
        attr = {x[0]: x[1:] for x in f[1:] if x}
        tags = {}
        if attr.get("T"):
            for kv in attr["T"].split(","):
                k, _, v = kv.partition("=")
                tags[_unesc(k)] = _unesc(v)
        if kind == "n":
            if not attr.get("x") or not attr.get("y"): continue
            e = {"type": "node", "id": oid, "lat": float(attr["y"]), "lon": float(attr["x"])}
            if tags: e["tags"] = tags
            el.append(e)
        elif kind == "r":
            mem = []
            for m in (attr.get("M") or "").split(","):
                if not m: continue
                ref, _, role = m.partition("@")
                mem.append({"type": {"n": "node", "w": "way", "r": "relation"}[ref[0]], "ref": int(ref[1:]), "role": _unesc(role)})
            el.append({"type": "relation", "id": oid, "tags": tags, "members": mem})
    return el

def leggi_strade(path):
    ways = []
    for line in open(path, encoding="utf-8"):
        line = line.strip().lstrip("\x1e")
        if not line: continue
        ft = json.loads(line)
        g = ft.get("geometry") or {}
        if g.get("type") != "LineString": continue
        p = dict(ft.get("properties") or {})
        wid = p.pop("@id", None)
        ways.append({"type": "way", "id": wid, "tags": p,
                     "geometry": [{"lat": c[1], "lon": c[0]} for c in g["coordinates"]]})
    return ways

def main():
    import strade
    scarica_pbf()
    # 1) autovelox + relazioni enforcement (con i nodi membri)
    sh("osmium", "tags-filter", str(PBF), "n/highway=speed_camera", "r/type=enforcement", "-O", "-o", "cams.osm.pbf")
    sh("osmium", "cat", "cams.osm.pbf", "-O", "-o", "cams.opl")
    el = leggi_opl("cams.opl")
    cams = [e for e in el if e["type"] == "node" and e.get("tags", {}).get("highway") == "speed_camera"]
    rel = [e for e in el if e["type"] == "relation" and e.get("tags", {}).get("enforcement", "") in ("maxspeed", "average_speed", "mindistance")]
    tenuti = {m["ref"] for r in rel for m in r["members"] if m["type"] == "node"} | {c["id"] for c in cams}
    osm = [e for e in el if (e["type"] == "node" and e["id"] in tenuti)] + rel
    pathlib.Path("osm_velox.json").write_text(json.dumps({"elements": osm}))
    print(f"nodi speed_camera: {len(cams)}  con direction: {sum('direction' in c.get('tags', {}) for c in cams)}  relazioni: {len(rel)}")
    if len(cams) < 4000: sys.exit("troppo pochi autovelox: estratto incompleto?")
    # 2) strade vicine: estratto con un riquadro di ~45 m per autovelox, poi solo le highway utili
    poli = [[[[c["lon"] - BOX_LON, c["lat"] - BOX_LAT], [c["lon"] + BOX_LON, c["lat"] - BOX_LAT],
              [c["lon"] + BOX_LON, c["lat"] + BOX_LAT], [c["lon"] - BOX_LON, c["lat"] + BOX_LAT],
              [c["lon"] - BOX_LON, c["lat"] - BOX_LAT]]] for c in cams]
    pathlib.Path("riquadri.geojson").write_text(json.dumps(
        {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": poli}}))
    sh("osmium", "extract", "-p", "riquadri.geojson", "-s", "complete_ways", str(PBF), "-O", "-o", "vicino.osm.pbf")
    sh("osmium", "tags-filter", "vicino.osm.pbf", "w/highway=" + CLASSI, "-O", "-o", "strade.osm.pbf")
    sh("osmium", "export", "strade.osm.pbf", "-f", "geojsonseq", "-a", "id", "--geometry-types=linestring", "-O", "-o", "strade.geojsonseq")
    ways = leggi_strade("strade.geojsonseq")
    res = strade.calcola(cams, ways, osm)
    pathlib.Path("strade.json").write_text(json.dumps(res, ensure_ascii=False))
    print(f"strade: {len(ways)} | autovelox con asse stradale: {sum('asse' in v for v in res.values())}/{len(cams)} | "
          f"senso unico: {sum(len(v.get('asse', [])) == 1 for v in res.values())} | "
          f"direzione da relazione: {sum(v['dir_rel'] is not None for v in res.values())}")

if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    main()
