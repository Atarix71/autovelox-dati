#!/usr/bin/env python3
"""Fase 1c - scarica tutto il registro MIT da /dispositivi/data (endpoint DataTables)."""
import json, csv, re, ssl, collections, urllib.request, urllib.parse, pathlib
URL = "https://velox.mit.gov.it/dispositivi/data"
H = {"User-Agent": "Mozilla/5.0 (Macintosh) autovelox-research/0.1",
     "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
     "Referer": "https://velox.mit.gov.it/dispositivi"}
ctx = ssl.create_default_context()
def call(params=None, post=False):
    body = urllib.parse.urlencode(params or {}).encode()
    url = URL if (post or not params) else URL + "?" + body.decode()
    req = urllib.request.Request(url, data=body if post else None, headers=H)
    raw = urllib.request.urlopen(req, timeout=60, context=ctx).read().decode("utf-8", "replace")
    try: return json.loads(raw)
    except Exception: print("[non-JSON]", raw[:600]); raise
import time, sys
for t in range(4):
    try:
        d = call(); break
    except Exception as e:
        print("  MIT tentativo", t + 1, "fallito:", e, file=sys.stderr); time.sleep(30 * (t + 1))
else:
    sys.exit("registro MIT non raggiungibile")
print("[GET semplice] chiavi:", list(d) if isinstance(d, dict) else type(d).__name__)
rows = d.get("data", d) if isinstance(d, dict) else d
tot = d.get("recordsTotal") if isinstance(d, dict) else None
print("righe ricevute:", len(rows), " recordsTotal:", tot)
if tot and len(rows) < int(tot):          # paginazione lato server
    rows, start = [], 0
    while True:
        for post in (False, True):
            try: p = call({"draw": 1, "start": start, "length": 1000}, post); break
            except Exception as e: print("tentativo", "POST" if post else "GET", "fallito:", e)
        chunk = p.get("data", [])
        rows += chunk; start += len(chunk)
        print(f"  ...{len(rows)}/{p.get('recordsTotal')}")
        if not chunk or len(rows) >= int(p.get("recordsTotal", 0)): break
pathlib.Path("mit_registro.json").write_text(json.dumps(rows, ensure_ascii=False))
print("[esempio riga]", json.dumps(rows[0], ensure_ascii=False)[:800])
if isinstance(rows[0], dict):
    keys = list(rows[0])
    with open("mit_registro.csv", "w", newline="") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print("[campi]", keys)
    flat = lambda r: " ".join(str(v) for v in r.values())
else:
    flat = lambda r: " ".join(map(str, r))
txt = [flat(r) for r in rows]
print("dispositivi:", len(rows))
print("con 'omolog' nel testo:", sum(bool(re.search(r"omolog", t, re.I)) for t in txt))
dec = collections.Counter(re.findall(r"\b\d{2,6}\b", " ".join(txt)))
for k in ("decreto", "estremi_decreto", "numero_decreto"):
    if isinstance(rows[0], dict) and k in rows[0]:
        dec = collections.Counter(str(r[k]).strip() for r in rows); break
print("decreti più frequenti:", dec.most_common(25))
