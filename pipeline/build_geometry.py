"""Contours simplifiés des communes pour la carte (Etalab, admin-express simplifié 100 m).

Sortie : public/data/communes.topojson (propriété unique `c` = code INSEE), simplifiée avec
mapshaper (devDependency npm : lancer `npm install` avant).
"""
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, PUBLIC_DATA, ROOT, download, normalize_insee  # noqa: E402

URL = "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/communes-100m.geojson.gz"


def rnd(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 4), round(coords[1], 4)]
    out = [rnd(c) for c in coords]
    # supprime les points consécutifs identiques après arrondi
    if out and isinstance(out[0][0], float):
        ded = [out[0]]
        for p in out[1:]:
            if p != ded[-1]:
                ded.append(p)
        if len(ded) < 4:
            ded = out
        return ded
    return out


def main():
    src = download(URL, "communes-100m.geojson.gz")
    gj = json.loads(gzip.decompress(src.read_bytes()))
    feats = []
    for f in gj["features"]:
        code = normalize_insee(f["properties"]["code"])
        g = f["geometry"]
        feats.append({"type": "Feature", "properties": {"c": code},
                      "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])}})
    tmp = CACHE / "communes_rounded.geojson"
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = PUBLIC_DATA / "communes.topojson"
    mapshaper = ROOT.parent / "node_modules" / ".bin" / "mapshaper"
    subprocess.run([str(mapshaper), str(tmp), "-rename-layers", "communes", "-simplify", "25%", "keep-shapes",
                    "-o", "format=topojson", "quantization=100000", str(out)], check=True)
    print(f"  → {out} ({len(feats)} communes, {out.stat().st_size / 1e6:.1f} Mo)", file=sys.stderr)


if __name__ == "__main__":
    main()
