#!/usr/bin/env python3
"""Limiti di velocità delle strade italiane (tag maxspeed di OpenStreetMap), per mostrare il limite in tempo reale.
Strade divise in celle di 0,05° (~5 km); ogni cella è un blob binario compatto che l'app carica solo quando serve.
Formato cella: varint(n strade); per strada varint(limite verso del disegno), varint(limite verso opposto; 0 = non
percorribile o ignoto), varint(n punti), poi delta zigzag di lat/lon in 1e-5 gradi (il primo dall'origine della cella).
Output: limiti.sqlite (tabella limiti(cella INTEGER PRIMARY KEY, dati BLOB))."""
import collections, json, math, pathlib, re, sqlite3, subprocess, sys, time

C = 0.05
CLASSI = ("motorway,trunk,primary,secondary,tertiary,unclassified,residential,living_street,road,service,"
          "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link")
TIPI = {"it:urban": 50, "it:rural": 90, "it:motorway": 130, "it:trunk": 110, "it:living_street": 10, "it:zone30": 30}

def kmh(v):
    if not v: return 0
    v = str(v).strip().lower()
    if v in TIPI: return TIPI[v]
    m = re.match(r"^(\d{1,3})\s*(mph)?", v)
    if not m: return 0
    n = int(m.group(1))
    if m.group(2): n = round(n * 1.609)
    return n if 5 <= n <= 150 else 0

def senso(t):
    o = t.get("oneway", "")
    if o in ("yes", "true", "1"): return 1
    if o == "-1": return -1
    if o == "no": return 0
    if t.get("highway") in ("motorway", "motorway_link") or t.get("junction") in ("roundabout", "circular"): return 1
    return 0

def _varint(n, out):
    while True:
        b = n & 0x7F; n >>= 7
        if n: out.append(b | 0x80)
        else: out.append(b); return

def _zz(v, out): _varint(v * 2 if v >= 0 else -v * 2 - 1, out)

def dp(p, toll=4.0):
    if len(p) < 3: return p
    k = math.cos(math.radians(p[0][1]))
    def xy(a): return ((a[0] - p[0][0]) * k * 111320, (a[1] - p[0][1]) * 110540)
    ax, ay = xy(p[0]); bx, by = xy(p[-1]); L = math.hypot(bx - ax, by - ay) or 1e-9
    dmax, imax = -1, 0
    for i in range(1, len(p) - 1):
        px, py = xy(p[i]); d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
        if d > dmax: dmax, imax = d, i
    if dmax <= toll: return [p[0], p[-1]]
    return dp(p[:imax + 1], toll)[:-1] + dp(p[imax:], toll)

def main(pbf):
    t0 = time.time()
    subprocess.run(["osmium", "tags-filter", str(pbf), "w/maxspeed,maxspeed:forward,maxspeed:backward,maxspeed:type", "-O", "-o", "limiti_tutte.osm.pbf"], check=True)
    subprocess.run(["osmium", "tags-filter", "limiti_tutte.osm.pbf", "w/highway=" + CLASSI, "-O", "-o", "limiti.osm.pbf"], check=True)
    subprocess.run(["osmium", "export", "limiti.osm.pbf", "-f", "geojsonseq", "--geometry-types=linestring", "-O", "-o", "limiti.geojsonseq"], check=True)
    celle = collections.defaultdict(list)
    n_vie = n_punti = 0
    for line in open("limiti.geojsonseq", encoding="utf-8"):
        line = line.strip().lstrip("\x1e")
        if not line: continue
        ft = json.loads(line); g = ft.get("geometry") or {}
        if g.get("type") != "LineString": continue
        t = ft.get("properties") or {}
        base = kmh(t.get("maxspeed")) or kmh(t.get("maxspeed:type"))
        fwd, bwd = kmh(t.get("maxspeed:forward")) or base, kmh(t.get("maxspeed:backward")) or base
        su = senso(t)
        if su == 1: bwd = 0
        if su == -1: fwd = 0
        if not fwd and not bwd: continue
        pts = dp([(c[0], c[1]) for c in g["coordinates"]])
        # tratti al massimo di ~1,5 km: così ogni cella toccata contiene un pezzo della strada
        fitti = [pts[0]]
        for q in pts[1:]:
            pa = fitti[-1]
            n = int(math.hypot((q[0] - pa[0]) * 78000, (q[1] - pa[1]) * 111000) // 1500)
            for k in range(1, n + 1):
                fitti.append((pa[0] + (q[0] - pa[0]) * k / (n + 1), pa[1] + (q[1] - pa[1]) * k / (n + 1)))
            fitti.append(q)
        pts = fitti
        n_vie += 1; n_punti += len(pts)
        # la strada va in ogni cella che tocca (spezzata cella per cella, con un punto di sovrapposizione)
        tratto, cella_corr = [], None
        for p in pts:
            cl = (math.floor(p[1] / C), math.floor(p[0] / C))
            if cella_corr is not None and cl != cella_corr:
                tratto.append(p)
                celle[cella_corr].append((fwd, bwd, tratto))
                tratto = [tratto[-2]]
            tratto.append(p); cella_corr = cl
        if len(tratto) >= 2: celle[cella_corr].append((fwd, bwd, tratto))
    db = pathlib.Path("limiti.sqlite"); db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE limiti(cella INTEGER PRIMARY KEY, dati BLOB)")
    righe, tot = [], 0
    for (i, j), vie in celle.items():
        out = bytearray(); _varint(len(vie), out)
        oa, oo = round(i * C * 1e5), round(j * C * 1e5)
        for fwd, bwd, pts in vie:
            _varint(fwd, out); _varint(bwd, out); _varint(len(pts), out)
            pa, po = oa, oo
            for lo, la in pts:
                a, b = round(la * 1e5), round(lo * 1e5)
                _zz(a - pa, out); _zz(b - po, out); pa, po = a, b
        righe.append((i * 100000 + j, bytes(out))); tot += len(out)
    con.executemany("INSERT INTO limiti VALUES(?,?)", righe)
    con.commit(); con.execute("VACUUM"); con.close()
    print(f"limiti: {n_vie} strade, {n_punti} punti, {len(celle)} celle, {tot >> 10} KB, {time.time() - t0:.0f} s", flush=True)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "italy-latest.osm.pbf")
