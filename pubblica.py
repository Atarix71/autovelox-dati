#!/usr/bin/env python3
"""Controlla il dataset e lo prepara per la pubblicazione: out/velox.db + out/manifest.json."""
import json, hashlib, sqlite3, shutil, pathlib, sys, datetime
MIN_POSTAZIONI, MIN_REGISTRO = 4000, 3000      # sotto queste soglie qualcosa è andato storto: non pubblico
db = pathlib.Path("velox.db")
con = sqlite3.connect(db)
n = con.execute("select count(*) from postazioni").fetchone()[0]
ver = con.execute("select v from meta where k='versione'").fetchone()[0]
stati = dict(con.execute("select stato, count(*) from postazioni group by stato").fetchall())
con.close()
reg = len(json.load(open("mit_registro.json")))
print(f"postazioni {n}, registro MIT {reg}, stati {stati}")
if n < MIN_POSTAZIONI or reg < MIN_REGISTRO:
    sys.exit(f"CONTROLLO FALLITO: dataset troppo piccolo, non pubblico")
out = pathlib.Path("../out"); out.mkdir(exist_ok=True)
shutil.copy(db, out / "velox.db")
raw = (out / "velox.db").read_bytes()
man = {"schema": 1, "versione": ver, "file": "velox.db", "bytes": len(raw),
       "sha256": hashlib.sha256(raw).hexdigest(), "postazioni": n, "registro_mit": reg, "stati": stati,
       "generato": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "fonti": "OpenStreetMap contributors (ODbL); MIT velox.mit.gov.it; DM 8/6/2026 n.125 All. B; ISTAT"}
(out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
(out / ".nojekyll").write_text("")
print("pubblicazione pronta:", man["versione"], man["sha256"][:12])
