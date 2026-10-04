#!/bin/bash
# Rigenera il dataset autovelox: OSM + registro MIT + Allegato B -> out/velox.db + out/manifest.json
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
mkdir -p work out
cp allegato_b.json work/
cd work
$PY ../scarica_osm.py
$PY ../scarica_mit.py | grep -E "righe ricevute|dispositivi:|campi" || true
test -s mit_registro.json
$PY ../classifica.py 2>&1 | grep -v "Possible issue" | grep -E "decreti|postazioni|stato post|velox.db"
$PY ../pubblica.py
cp ../sito/*.html ../out/
