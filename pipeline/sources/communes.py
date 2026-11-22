"""Référentiel des communes (geo.api.gouv.fr) : nom, département, centre, population, surface."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import download, write_csv, write_indicators  # noqa: E402

URL = ("https://geo.api.gouv.fr/communes?fields=nom,code,codesPostaux,centre,surface,"
       "population,codeDepartement,codeRegion&format=json")


def main():
    data = json.loads(download(URL, "geoapi_communes.json").read_text(encoding="utf-8"))
    rows = []
    for c in data:
        if not c.get("centre"):
            continue
        lon, lat = c["centre"]["coordinates"]
        surf = (c.get("surface") or 0) / 100  # hectares -> km²
        pop = c.get("population")
        rows.append({
            "code_insee": c["code"],
            "nom": c["nom"],
            "dep": c.get("codeDepartement", ""),
            "region": c.get("codeRegion", ""),
            "cp": c["codesPostaux"][0] if c.get("codesPostaux") else "",
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "population": pop,
            "surface_km2": round(surf, 2) if surf else None,
            "densite": round(pop / surf, 1) if pop and surf else None,
        })
    write_csv("communes", rows, ["nom", "dep", "region", "cp", "lat", "lon", "population", "surface_km2", "densite"])
    write_indicators("communes", [
        {"id": "population", "label": "Population", "group": "Démographie", "unit": "hab.", "type": "number",
         "better": None, "description": "Population municipale", "source": "INSEE via geo.api.gouv.fr",
         "decimals": 0, "scale": "log"},
        {"id": "densite", "label": "Densité", "group": "Démographie", "unit": "hab./km²", "type": "number",
         "better": None, "description": "Habitants par km²", "source": "INSEE via geo.api.gouv.fr",
         "decimals": 0, "scale": "log"},
        {"id": "surface_km2", "label": "Superficie", "group": "Démographie", "unit": "km²", "type": "number",
         "better": None, "description": "Superficie de la commune", "source": "IGN via geo.api.gouv.fr",
         "decimals": 1, "scale": "log"},
    ])


if __name__ == "__main__":
    main()
