#!/usr/bin/env python3
"""Fase 1f v2 - incrocia OSM + registro MIT + Allegato B -> velox.db (dataset per l'app)."""
import json, re, io, zipfile, csv, sqlite3, collections, datetime, pathlib, urllib.request, html, unicodedata
import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
UA = {"User-Agent": "autovelox-research/0.1"}
IST = pathlib.Path("istat"); IST.mkdir(exist_ok=True)

def scarica(url, dest):
    if not dest.exists():
        print("  scarico", url)
        dest.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180).read())
    return dest

def carica_comuni():
    z = None
    for anno in (2026, 2025, 2024):
        try:
            z = scarica(f"https://www.istat.it/storage/cartografia/confini_amministrativi/generalizzati/{anno}/Limiti0101{anno}_g.zip",
                        IST / f"Limiti0101{anno}_g.zip"); break
        except Exception as e: print("  ", anno, "n/d:", e)
    if z is None: raise SystemExit("confini ISTAT non scaricabili")
    zf = zipfile.ZipFile(z)
    shp = [n for n in zf.namelist() if re.search(r"Com\d+_g_WGS84\.shp$", n)]
    if not shp: raise SystemExit("shapefile comuni non trovato nello zip: " + str(zf.namelist()[:30]))
    base = shp[0][:-4]
    r = shapefile.Reader(shp=io.BytesIO(zf.read(base + ".shp")), dbf=io.BytesIO(zf.read(base + ".dbf")),
                         shx=io.BytesIO(zf.read(base + ".shx")), encoding="utf-8")
    campi = [f[0] for f in r.fields[1:]]
    el = scarica("https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv",
                 IST / "Elenco-comuni-italiani.csv").read_bytes().decode("latin-1")
    rd = csv.reader(io.StringIO(el), delimiter=";"); hdr = next(rd)
    i_ist = next(i for i, h in enumerate(hdr) if "alfanumerico" in h.lower())
    i_cat = next(i for i, h in enumerate(hdr) if "catastale" in h.lower())
    i_nom = next(i for i, h in enumerate(hdr) if "denominazione in italiano" in h.lower() or h.lower().startswith("denominazione"))
    i_sig = next((i for i, h in enumerate(hdr) if "sigla" in h.lower()), None)
    ist2cat, nom2cat, CAT2SIG = {}, {}, {}
    for row in rd:
        if len(row) <= i_cat: continue
        ist2cat[row[i_ist]] = row[i_cat]; nom2cat[norm_nome(row[i_nom])] = row[i_cat]
        if i_sig is not None: CAT2SIG[row[i_cat]] = row[i_sig]
    geoms, info = [], []
    for sr in r.iterShapeRecords():
        rec = dict(zip(campi, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        nome = rec.get("COMUNE")
        info.append((nome, ist2cat.get(rec.get("PRO_COM_T")) or nom2cat.get(norm_nome(nome), "")))
    print(f"  comuni: {len(geoms)}  con codice catastale: {sum(bool(c) for _, c in info)}")
    proj = None
    if geoms[0].bounds[0] > 180:
        from pyproj import Transformer
        proj = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
        print("  confini in UTM32N: riproietto i punti GPS")
    CENTRO = {c: geoms[i].centroid for i, (_, c) in enumerate(info) if c}
    return STRtree(geoms), geoms, info, proj, CAT2SIG, CENTRO

def norm_nome(n):
    n = html.unescape(str(n or "")).split("/")[0].lower()
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", n)

def punto(proj, lon, lat):
    return Point(*proj.transform(lon, lat)) if proj else Point(lon, lat)

def trova_comune(tree, geoms, info, proj, lon, lat):
    p = punto(proj, lon, lat)
    for i in tree.query(p):
        if geoms[i].covers(p): return info[i]
    i = tree.nearest(p)
    if proj and geoms[i].distance(p) < 2000: return info[i]
    return (None, "")

RX_MOB = re.compile(r"mobil|molbil|mibil|telelaser|teleser|laser|portatil|trucam|tru-?cam|scout|ultral|pistola|a bordo|temporane|dinamic|manuale|presidiat", re.I)
RX_FIX = re.compile(r"fiss|fissa|media|postaz|sede|varco|bidirez|monodirez|stazione|non presidiat", re.I)
RX_MOD_MOB = re.compile(r"telelaser|trucam|truspeed|ultral|scout|lti", re.I)
def tipo(r):
    t = html.unescape(str(r.get("tipo_dispositivo") or ""))
    f, m = bool(RX_FIX.search(t)), bool(RX_MOB.search(t))
    if f and m: return "fisso_mobile"
    if f: return "fisso"
    if m: return "mobile"
    if RX_MOD_MOB.search(f'{r.get("marca_dispositivo")} {r.get("modello_dispositivo")} {r.get("versione_dispositivo")}'): return "mobile"
    return "ignoto"

RX_PROV = re.compile(r"provincia|citt[aà]\s+metropolitana|libero consorzio|ente di decentramento", re.I)
RX_UNIONE = re.compile(r"unione|intercomunal|associat|consorzio|comunit[aà] montana|convenzion|ambito|distretto", re.I)

RX_DEC = re.compile(r"0*(\d{1,7})\s*(?:del\S{0,2}|-)\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", re.I)
def norm_data(s):
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", str(s or ""))
    if m: return f"{int(m[1]):02d}/{int(m[2]):02d}/{m[3]}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    return f"{m[3]}/{m[2]}/{m[1]}" if m else ""

def stato_dispositivo(r, allB):
    decreti = set()
    n = re.sub(r"\D", "", str(r.get("n_decreto") or ""))
    if n: decreti.add((int(n), norm_data(r.get("data_decreto"))))
    note = html.unescape(str(r.get("note") or ""))
    for m in RX_DEC.finditer(note):
        decreti.add((int(m[1]), f"{int(m[2]):02d}/{int(m[3]):02d}/{m[4]}"))
    if decreti & allB: return "omologato", "decreto in Allegato B DM 125/2026"
    if re.search(r"(?<!non )omologa", note, re.I): return "omologato_dichiarato", "omologazione citata nelle note dell'ente"
    return "approvato", "solo decreto di approvazione"

def main():
    allB = {(d["n"], d["data"]) for d in json.load(open("allegato_b.json"))}
    reg = json.load(open("mit_registro.json"))
    osm = json.load(open("osm_velox.json"))["elements"]
    rep = []
    def log(*a):
        s = " ".join(map(str, a)); print(s); rep.append(s)
    log("decreti Allegato B:", len(allB), "| dispositivi registro:", len(reg))
    per_comune = collections.defaultdict(list)
    cnt = collections.Counter()
    for r in reg:
        st, why = stato_dispositivo(r, allB)
        tp = tipo(r)
        cnt[(st, tp)] += 1
        per_comune[str(r.get("codice_catastale_accertatore", "")).strip().upper()].append(
            {"stato": st, "fisso": tp != "mobile", "tipo": tp, "marca": r.get("marca_dispositivo"), "modello": r.get("modello_dispositivo"),
             "decreto": f'{r.get("n_decreto")} del {r.get("data_decreto")}',
             "ente": html.unescape(str(r.get("denominazione_accertatore", "")))})
    log("stato registro (stato, tipo) ->", sorted(cnt.items()))
    log("tipo dedotto:", collections.Counter(tipo(r) for r in reg).most_common(12))
    print("confini comunali...")
    tree, geoms, info, proj, CAT2SIG, CENTRO = carica_comuni()
    prov, unioni = collections.defaultdict(list), []
    for cat, ds in per_comune.items():
        for d in ds:
            if RX_PROV.search(d["ente"]) and CAT2SIG.get(cat): prov[CAT2SIG[cat]].append(d)
            elif RX_UNIONE.search(d["ente"]) and cat in CENTRO: unioni.append((CENTRO[cat], d))
    log("enti provinciali (sigle):", len(prov), "| dispositivi di unioni/intercomunali:", len(unioni))
    cams = [e for e in osm if e["type"] == "node" and e.get("tags", {}).get("highway") == "speed_camera"]
    for c in cams:
        c["_comune"], c["_cat"] = trova_comune(tree, geoms, info, proj, c["lon"], c["lat"])
    osm_per_comune = collections.Counter(c["_cat"] for c in cams)
    strade = json.load(open("strade.json")) if pathlib.Path("strade.json").exists() else {}
    log("asse stradale disponibile per:", sum("asse" in v for v in strade.values()), "postazioni")
    righe, dist = [], collections.Counter()
    for c in cams:
        t, cat = c.get("tags", {}), c["_cat"]
        disp = per_comune.get(cat, [])
        fissi = [d for d in disp if d["fisso"]] or disp
        stati = collections.Counter(d["stato"] for d in fissi)
        fonte = "comune"
        if not fissi:
            p = punto(proj, c["lon"], c["lat"])
            vic = [d for cen, d in unioni if cen.distance(p) < 20000]
            if not vic and cat: vic = prov.get(CAT2SIG.get(cat, ""), [])
            if vic:
                fonte = "sovracomunale"
                fissi = [d for d in vic if d["fisso"]] or vic
                stati = collections.Counter(d["stato"] for d in fissi)
        if not fissi:
            stato, aff = "non_censito", "bassa"
        elif fonte == "sovracomunale":
            stato, aff = (next(iter(stati)) if len(stati) == 1 else "misto"), "bassa"
        elif len(stati) == 1:
            stato = next(iter(stati))
            aff = "alta" if len([d for d in disp if d["fisso"]]) >= osm_per_comune[cat] else "media"
        else:
            stato, aff = "misto", "bassa"
        dist[(stato, aff)] += 1
        st = strade.get(str(c["id"]), {})
        direzione = t.get("direction") or (str(st["dir_rel"]) if st.get("dir_rel") is not None else None)
        righe.append({"id": c["id"], "lat": c["lat"], "lon": c["lon"],
                      "maxspeed": t.get("maxspeed"), "direction": direzione,
                      "strada_dir": ";".join(str(x) for x in st.get("asse", [])) or None,
                      "strada_tipo": st.get("tipo"), "strada_nome": st.get("nome"),
                      "comune": c["_comune"], "cod_catastale": cat, "stato": stato, "affidabilita": aff,
                      "dettaglio": {"fonte": fonte, "stati_comune": dict(stati),
                                    "dispositivi": [{k: d[k] for k in ("marca", "modello", "decreto", "stato", "ente")} for d in fissi[:6]]}})
    log("postazioni OSM:", len(righe), "| fuori dai confini:", sum(not r["cod_catastale"] for r in righe))
    log("stato postazioni (stato, affidabilità) ->", sorted(dist.items(), key=lambda x: -x[1]))
    log("fonte:", collections.Counter(r["dettaglio"]["fonte"] for r in righe if r["stato"] != "non_censito").most_common())
    nc = collections.Counter(r["comune"] for r in righe if r["stato"] == "non_censito")
    log("comuni con più postazioni non censite:", nc.most_common(15))
    pathlib.Path("postazioni.json").write_text(json.dumps(righe, ensure_ascii=False))
    db = pathlib.Path("velox.db"); db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE postazioni(id INTEGER PRIMARY KEY, lat REAL, lon REAL, maxspeed TEXT,
        direction TEXT, comune TEXT, cod_catastale TEXT, stato TEXT, affidabilita TEXT, dettaglio TEXT,
        strada_dir TEXT, strada_tipo TEXT, strada_nome TEXT);
        CREATE INDEX ix_latlon ON postazioni(lat, lon);
        CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);""")
    con.executemany("INSERT INTO postazioni VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["id"], r["lat"], r["lon"], r["maxspeed"], r["direction"], r["comune"], r["cod_catastale"],
          r["stato"], r["affidabilita"], json.dumps(r["dettaglio"], ensure_ascii=False),
          r["strada_dir"], r["strada_tipo"], r["strada_nome"]) for r in righe])
    ver = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
    con.executemany("INSERT INTO meta VALUES(?,?)", [("versione", ver),
        ("fonti", "OpenStreetMap contributors (ODbL); MIT velox.mit.gov.it; DM 8/6/2026 n.125 All. B; ISTAT")])
    con.commit(); con.close()
    log(f"velox.db scritto ({db.stat().st_size // 1024} KB), versione {ver}")
    pathlib.Path("classifica_report.txt").write_text("\n".join(rep))

if __name__ == "__main__":
    main()
