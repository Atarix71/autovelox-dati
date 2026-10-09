#!/usr/bin/env python3
"""Corridoi di avvicinamento: per ogni autovelox, le strade (nel verso di marcia consentito) che portano
alla postazione nei 2,3 km precedenti, con la distanza su strada da ogni punto all'autovelox.
L'app avvisa solo se l'auto si trova su uno di questi corridoi e procede verso l'autovelox:
così sono esclusi gli autovelox su strade parallele, incroci, cavalcavia e corsie opposte.
Input: grafo.geojsonseq (strade nei dintorni, da osmium), osm_velox.json. Output: corridoi.json
{ "<id>": "lat,lon,dist;lat,lon,dist|..." }  (catene ordinate dal punto lontano verso l'autovelox)."""
import collections, heapq, json, math, pathlib, re, sys

MAXD = 2300          # metri di strada a monte dell'autovelox
AGGANCIO = 30        # distanza massima autovelox-strada
MAX_NODI = 8000      # limite di sicurezza per postazione (centri urbani)
TOLL_DP = 3.0        # semplificazione Douglas-Peucker (m)
RANGO = {"motorway": 1, "motorway_link": 1, "trunk": 2, "trunk_link": 2, "primary": 3, "primary_link": 3,
         "secondary": 4, "secondary_link": 4, "tertiary": 5, "tertiary_link": 5, "unclassified": 6, "road": 6,
         "residential": 7, "living_street": 8, "service": 8}
CARD = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5, "S": 180,
        "SSW": 202.5, "SW": 225, "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5, "O": 270, "NO": 315, "SO": 225}

def xy(lat0, lon0, lat, lon):
    k = math.cos(math.radians(lat0))
    return ((lon - lon0) * k * 111320, (lat - lat0) * 110540)

def lung(a, b):
    x, y = xy(a[1], a[0], b[1], b[0]); return math.hypot(x, y)

def bearing(a, b):
    p1, p2, dl = math.radians(a[1]), math.radians(b[1]), math.radians(b[0] - a[0])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def angdiff(a, b): return abs((a - b + 540) % 360 - 180)

def senso(t):
    o = t.get("oneway", "")
    if o in ("yes", "true", "1"): return 1
    if o == "-1": return -1
    if o == "no": return 0
    if t.get("highway") in ("motorway", "motorway_link") or t.get("junction") in ("roundabout", "circular"): return 1
    return 0

def direzioni(t):
    out, rel = [], set()
    for p in str(t.get("direction", "")).replace(",", ";").split(";"):
        p = p.strip().upper()
        if not p: continue
        try: out.append(float(p) % 360)
        except ValueError:
            if p in CARD: out.append(CARD[p])
            elif p in ("FORWARD", "BACKWARD"): rel.add(p)
    return out, rel

def dp(punti, toll):
    """Douglas-Peucker su [(lon,lat,dist)]."""
    if len(punti) < 3: return punti
    a, b = punti[0], punti[-1]
    ax, ay = 0.0, 0.0
    bx, by = xy(a[1], a[0], b[1], b[0])
    L = math.hypot(bx, by) or 1e-9
    dmax, imax = -1, 0
    for i in range(1, len(punti) - 1):
        px, py = xy(a[1], a[0], punti[i][1], punti[i][0])
        d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
        if d > dmax: dmax, imax = d, i
    if dmax <= toll: return [a, b]
    return dp(punti[:imax + 1], toll)[:-1] + dp(punti[imax:], toll)

def _varint(n, out):
    while True:
        b = n & 0x7F; n >>= 7
        if n: out.append(b | 0x80)
        else: out.append(b); return

def codifica(testo):
    """Testo "lat,lon,dist;...|..." -> binario compatto: varint(n catene), per catena varint(n punti), poi per punto
    delta zigzag di lat e lon (1e-5 gradi, ~1 m) e della distanza (m) rispetto al punto precedente. ~6 byte/punto."""
    out = bytearray()
    catene = [[p.split(",") for p in ch.split(";")] for ch in testo.split("|")]
    _varint(len(catene), out)
    pa = po = pd = 0
    for ch in catene:
        _varint(len(ch), out)
        for la, lo, d in ch:
            a, b, c = round(float(la) * 1e5), round(float(lo) * 1e5), int(round(float(d)))
            for v in (a - pa, b - po, c - pd):
                _varint(v * 2 if v >= 0 else -v * 2 - 1, out)
            pa, po, pd = a, b, c
    return bytes(out)

def main():
    osm = json.load(open("osm_velox.json"))["elements"]
    cams = [e for e in osm if e["type"] == "node" and e.get("tags", {}).get("highway") == "speed_camera"]
    nodi, coord = {}, []
    inc = collections.defaultdict(list)       # v -> [(u, lunghezza, rango)] archi u->v percorribili
    usc = collections.defaultdict(list)       # u -> [(v, lunghezza)] (per le tratte Tutor)
    seg = collections.defaultdict(list)       # cella -> [(a, b, rango, senso)]
    cella = lambda lon, lat: (int(lat / 0.0025), int(lon / 0.0025))
    def nodo(c):
        k = (round(c[0], 7), round(c[1], 7))
        i = nodi.get(k)
        if i is None:
            i = nodi[k] = len(coord); coord.append(k)
        return i
    n_way = 0
    for line in open("grafo.geojsonseq", encoding="utf-8"):
        line = line.strip().lstrip("\x1e")
        if not line: continue
        ft = json.loads(line); g = ft.get("geometry") or {}
        if g.get("type") != "LineString": continue
        t = ft.get("properties") or {}
        r = RANGO.get(t.get("highway"))
        if r is None: continue
        su = senso(t); n_way += 1
        ids = [nodo(c) for c in g["coordinates"]]
        for a, b in zip(ids, ids[1:]):
            if a == b: continue
            L = lung(coord[a], coord[b])
            if su >= 0: inc[b].append((a, L, r)); usc[a].append((b, L))
            if su <= 0: inc[a].append((b, L, r)); usc[b].append((a, L))
            m = ((coord[a][0] + coord[b][0]) / 2, (coord[a][1] + coord[b][1]) / 2)
            for p in (coord[a], coord[b], m):
                seg[cella(*p)].append((a, b, r, su))
    print(f"  grafo: {n_way} strade, {len(coord)} nodi", flush=True)

    out, stat = {}, collections.Counter()
    for c in cams:
        lon0, lat0 = c["lon"], c["lat"]
        ci, cj = cella(lon0, lat0)
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for a, b, r, su in seg.get((ci + di, cj + dj), ()):
                    ax, ay = xy(lat0, lon0, coord[a][1], coord[a][0]); bx, by = xy(lat0, lon0, coord[b][1], coord[b][0])
                    dx, dy = bx - ax, by - ay; LL = dx * dx + dy * dy
                    t = 0 if LL == 0 else max(0, min(1, (-ax * dx - ay * dy) / LL))
                    d = math.hypot(ax + t * dx, ay + t * dy)
                    if best is None or d < best[0]: best = (d, a, b, r, su, t)
        if not best or best[0] > AGGANCIO:
            stat["senza strada"] += 1; continue
        _, a, b, r, su, t = best
        P = (coord[a][0] + t * (coord[b][0] - coord[a][0]), coord[a][1] + t * (coord[b][1] - coord[a][1]))
        dirs, rel = direzioni(c.get("tags", {}))
        partenze = []   # (nodo di partenza, distanza dal punto P, nodo da non usare subito)
        if su >= 0: partenze.append((a, lung(coord[a], P), b, bearing(coord[a], coord[b]), "FORWARD"))
        if su <= 0: partenze.append((b, lung(coord[b], P), a, bearing(coord[b], coord[a]), "BACKWARD"))
        if dirs: partenze = [p for p in partenze if any(angdiff(p[3], x) <= 60 for x in dirs)] or partenze
        elif rel: partenze = [p for p in partenze if p[4] in rel] or partenze
        rmax = max(r, 5)
        catene = []
        for s, d0, vietato, _, _ in partenze:
            dist, padre = {s: d0}, {s: -1}
            coda, chiusi = [(d0, s)], set()
            while coda and len(chiusi) < MAX_NODI:
                d, v = heapq.heappop(coda)
                if v in chiusi: continue
                chiusi.add(v)
                for u, L, ru in inc.get(v, ()):
                    if ru > rmax or u == padre[v] or (v == s and u == vietato): continue
                    nd = d + L
                    if nd > MAXD or nd >= dist.get(u, 1e18): continue
                    dist[u], padre[u] = nd, v
                    heapq.heappush(coda, (nd, u))
            figli = collections.Counter(padre[v] for v in chiusi if padre[v] in chiusi)
            fatti = set()
            for foglia in (v for v in chiusi if figli[v] == 0):
                ch, v = [], foglia
                while v != -1 and v in chiusi:
                    ch.append((coord[v][0], coord[v][1], dist[v]))
                    if v in fatti: break
                    fatti.add(v); v = padre[v]
                if v == -1: ch.append((P[0], P[1], 0.0))   # arriva fino all'autovelox
                if len(ch) >= 2: catene.append(dp(ch, TOLL_DP))
        if not catene:
            stat["senza corridoio"] += 1; continue
        stat["con corridoio"] += 1
        out[str(c["id"])] = "|".join(";".join(f"{la:.5f},{lo:.5f},{int(round(d))}" for lo, la, d in ch) for ch in catene)
    pathlib.Path("corridoi.json").write_text(json.dumps(out))
    tutor(osm, coord, usc, seg, cella)
    punti = sum(v.count(";") + v.count("|") + 1 for v in out.values())
    print(f"corridoi: {dict(stat)} | punti {punti} | {pathlib.Path('corridoi.json').stat().st_size >> 10} KB")

def tutor(osm, coord, usc, seg, cella):
    """Tratte di controllo della velocità media (relazioni enforcement=average_speed con nodi from/to):
    percorso su strada da inizio a fine nel verso di marcia, lunghezza, limite. Output: tutor.json."""
    nodi = {e["id"]: e for e in osm if e["type"] == "node"}
    def aggancia(p, inizio):
        ci, cj = cella(p["lon"], p["lat"]); best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for a, b, r, su in seg.get((ci + di, cj + dj), ()):
                    for n in (a, b):
                        d = lung((p["lon"], p["lat"]), coord[n])
                        if best is None or d < best[0]: best = (d, n)
        return best[1] if best and best[0] <= 40 else None
    out, stat = [], collections.Counter()
    for r in (e for e in osm if e["type"] == "relation" and e.get("tags", {}).get("enforcement") == "average_speed"):
        m = {x.get("role"): nodi.get(x["ref"]) for x in r["members"] if x["type"] == "node"}
        a, b = m.get("from"), m.get("to")
        if not a or not b: stat["senza inizio/fine"] += 1; continue
        s, t = aggancia(a, True), aggancia(b, False)
        if s is None or t is None: stat["inizio/fine fuori strada"] += 1; continue
        aria = lung((a["lon"], a["lat"]), (b["lon"], b["lat"]))
        dist, padre, coda, chiusi = {s: 0.0}, {s: -1}, [(0.0, s)], set()
        while coda:
            d, v = heapq.heappop(coda)
            if v in chiusi: continue
            chiusi.add(v)
            if v == t or d > aria * 2.5 + 2000: break
            for u, L in usc.get(v, ()):
                nd = d + L
                if nd < dist.get(u, 1e18): dist[u], padre[u] = nd, v; heapq.heappush(coda, (nd, u))
        if t not in chiusi: stat["percorso non trovato"] += 1; continue
        if dist[t] < 300: stat["tratta troppo corta (dati OSM incompleti)"] += 1; continue
        via, v = [], t
        while v != -1: via.append(v); v = padre[v]
        via.reverse()
        punti = [(coord[v][0], coord[v][1], dist[v]) for v in via]
        punti = dp(punti, TOLL_DP)
        lim = re.search(r"\d+", r["tags"].get("maxspeed", "") or "")
        if not lim: stat["senza limite"] += 1; continue
        out.append({"id": r["id"], "nome": r["tags"].get("name") or r["tags"].get("description") or "Tutor",
                    "limite": int(lim.group()), "lunghezza": round(dist[t]),
                    "punti": ";".join(f"{la:.5f},{lo:.5f},{int(round(d))}" for lo, la, d in punti)})
        stat["tratte"] += 1
    pathlib.Path("tutor.json").write_text(json.dumps(out, ensure_ascii=False))
    print(f"tutor: {dict(stat)}")

if __name__ == "__main__":
    main()
