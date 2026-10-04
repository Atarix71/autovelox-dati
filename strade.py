#!/usr/bin/env python3
"""Per ogni autovelox OSM ricava l'asse della strada su cui si trova (entro 25 m) e,
dalle relazioni type=enforcement, la direzione controllata. Output: strade.json
{ "<id nodo>": {"asse": [gradi...], "tipo": "primary", "nome": "SS16", "dist": 3.2, "dir_rel": 87.5|null} }"""
import json, math, time, sys, urllib.request, urllib.parse, collections, pathlib

CLASSI = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|road|" \
         "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
QB = """[out:json][timeout:170];
node(id:{ids})->.c;
way(around.c:25)["highway"~"^({classi})$"];
out tags geom;"""
LOTTO = 100          # autovelox per richiesta: query leggere invece di una sola enorme
SERVER = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
          "https://overpass.private.coffee/api/interpreter"]
MAX_DIST = 25.0

def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def dist_seg(lat, lon, a, b):
    """distanza (m) punto-segmento in proiezione locale"""
    k = math.cos(math.radians(lat))
    px, py = lon * k * 111320, lat * 110540
    ax, ay, bx, by = a["lon"] * k * 111320, a["lat"] * 110540, b["lon"] * k * 111320, b["lat"] * 110540
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def senso_unico(tags):
    o = tags.get("oneway", "")
    if o in ("yes", "true", "1"): return 1
    if o == "-1": return -1
    if o == "no": return 0
    if tags.get("highway") in ("motorway", "motorway_link") or tags.get("junction") in ("roundabout", "circular"): return 1
    return 0

class Saturo(Exception):
    pass

def _richiesta(q):
    """Una richiesta con rotazione dei server. 429 = troppe richieste: aspetta e riprova."""
    for t in range(4):
        for url in SERVER:
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": "autovelox-dati/1.0 (github.com/Atarix71/autovelox-dati)"})
                return json.load(urllib.request.urlopen(req, timeout=200))["elements"]
            except urllib.error.HTTPError as e:
                print("  errore", url.split("/")[2], e, file=sys.stderr)
                time.sleep(60 if e.code == 429 else 10)
            except Exception as e:
                print("  errore", url.split("/")[2], e, file=sys.stderr)
                time.sleep(10)
        time.sleep(60 * (t + 1))
    raise Saturo()

CACHE = pathlib.Path("strade_db.json")   # {"fatti": [id autovelox], "ways": {id: way}} - si accumula tra un'esecuzione e l'altra
BUDGET_S = int(__import__("os").environ.get("STRADE_BUDGET_S", "1500"))   # tempo massimo per i download

def scarica(cams):
    """Scarica solo le strade degli autovelox non ancora in cache, a lotti di LOTTO,
    con pausa tra le richieste. Se i server sono saturi o finisce il tempo, prosegue con quanto ha."""
    db = json.loads(CACHE.read_text()) if CACHE.exists() else {"fatti": [], "ways": {}}
    fatti, ways = set(db["fatti"]), db["ways"]
    mancanti = [c["id"] for c in cams if c["id"] not in fatti]
    lotti = [mancanti[i:i + LOTTO] for i in range(0, len(mancanti), LOTTO)]
    print(f"  in cache: {len(fatti)} autovelox, da scaricare: {len(mancanti)} ({len(lotti)} lotti)", flush=True)
    t0 = time.time()
    for n, lotto in enumerate(lotti, 1):
        if time.time() - t0 > BUDGET_S:
            print("  tempo esaurito: completo alla prossima esecuzione", flush=True); break
        try:
            el = _richiesta(QB.format(ids=",".join(map(str, lotto)), classi=CLASSI))
        except Saturo:
            print("  server saturi: completo alla prossima esecuzione", flush=True); break
        for e in el:
            if e["type"] == "way": ways[str(e["id"])] = e
        fatti.update(lotto)
        CACHE.write_text(json.dumps({"fatti": sorted(fatti), "ways": ways}))
        print(f"  lotto {n}/{len(lotti)}: {len(ways)} strade", flush=True)
        time.sleep(8)   # cortesia verso i server pubblici
    return list(ways.values())

def calcola(cams, ways, osm_elements):
    # griglia ~250 m per cercare i segmenti vicini
    G = collections.defaultdict(list)
    cell = lambda la, lo: (int(la / 0.0025), int(lo / 0.0025))
    for w in ways:
        g = w.get("geometry") or []
        for i in range(len(g) - 1):
            a, b = g[i], g[i + 1]
            for la, lo in ((a["lat"], a["lon"]), (b["lat"], b["lon"]), ((a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2)):
                G[cell(la, lo)].append((w, i))
    nodi = {e["id"]: e for e in osm_elements if e["type"] == "node"}
    rel_dir = {}
    for r in (e for e in osm_elements if e["type"] == "relation"):
        m = {x.get("role"): x.get("ref") for x in r.get("members", []) if x.get("type") == "node"}
        dev, fr, to = nodi.get(m.get("device")), nodi.get(m.get("from")), nodi.get(m.get("to"))
        if not dev: continue
        if fr and to: rel_dir[dev["id"]] = bearing(fr["lat"], fr["lon"], to["lat"], to["lon"])
        elif to: rel_dir[dev["id"]] = bearing(dev["lat"], dev["lon"], to["lat"], to["lon"])
        elif fr: rel_dir[dev["id"]] = bearing(fr["lat"], fr["lon"], dev["lat"], dev["lon"])
    out = {}
    for c in cams:
        ci, cj = cell(c["lat"], c["lon"])
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for w, i in G.get((ci + di, cj + dj), ()):
                    g = w["geometry"]
                    d = dist_seg(c["lat"], c["lon"], g[i], g[i + 1])
                    if best is None or d < best[0]: best = (d, w, i)
        rec = {"dir_rel": round(rel_dir[c["id"]], 1) if c["id"] in rel_dir else None}
        if best and best[0] <= MAX_DIST:
            d, w, i = best
            g, t = w["geometry"], w.get("tags", {})
            b = bearing(g[i]["lat"], g[i]["lon"], g[i + 1]["lat"], g[i + 1]["lon"])
            su = senso_unico(t)
            asse = [b] if su == 1 else [(b + 180) % 360] if su == -1 else [b, (b + 180) % 360]
            rec.update({"asse": [round(x, 1) for x in asse], "tipo": t.get("highway"),
                        "nome": t.get("ref") or t.get("name") or "", "dist": round(d, 1)})
        out[str(c["id"])] = rec
    return out

if __name__ == "__main__":
    osm = json.load(open("osm_velox.json"))["elements"]
    cams = [e for e in osm if e["type"] == "node" and e.get("tags", {}).get("highway") == "speed_camera"]
    ways = scarica(cams)
    res = calcola(cams, ways, osm)
    pathlib.Path("strade.json").write_text(json.dumps(res, ensure_ascii=False))
    con_asse = sum("asse" in v for v in res.values())
    print(f"strade: {len(ways)} | autovelox con asse stradale: {con_asse}/{len(cams)} | "
          f"senso unico: {sum(len(v.get('asse', [])) == 1 for v in res.values())} | direzione da relazione: {sum(v['dir_rel'] is not None for v in res.values())}")
