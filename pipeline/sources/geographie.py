"""Cadre géographique des communes : altitude, littoral, distance à la mer, montagne, climat.

Sources :
  - Altitude : API altimétrie de la Géoplateforme IGN (RGE ALTI / MNT mondial), au centre de la commune
    (coordonnées de pipeline/out/communes.csv)
  - Loi Littoral : classement des communes (DGALN-SIDAUH), Observatoire des territoires (ANCT), COG 2026
  - Trait de côte : Natural Earth 10m coastline v5.1.2 (domaine public)
  - Zone de montagne : communes concernées par la loi Montagne de 1985 (DGALN-SIDAUH), COG 2022
  - Climat : fiches climatologiques Météo-France (statistiques 1991-2020 des stations), interpolées
    sur les communes (k plus proches stations, correction d'altitude pour la température)
"""
import concurrent.futures as cf
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CACHE, UA, download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

ALTI_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
ALTI_RESOURCE = "ign_rge_alti_wld"
# Au-delà de 30 points par requête, l'API rééchantillonne le MNT sur l'emprise du lot (valeurs dégradées) :
# on reste à 30 points, ce qui donne exactement les mêmes altitudes qu'en requêtes unitaires.
ALTI_BATCH = 30
ALTI_WORKERS = 4
ALTI_CACHE = CACHE / "geopf_altitude_communes.json"

LITTORAL_URL = ("https://www.observatoire-des-territoires.gouv.fr/outils/cartographie-interactive/api/v1/"
                "functions/GC_API_download.php?type=stat&nivgeo=com2026&dataset=typo_loilitt&indic=loilitt_simp")
COAST_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_coastline.geojson"
MONTAGNE_URL = ("https://static.data.gouv.fr/resources/communes-de-la-loi-montagne-au-code-officiel-geographique-"
                "cog-2020-2022/20220323-152301/dgaln-icapp-sidauh-opendata-loi-montagne-1985-cog-2022.xlsx")
COG_URL = "https://cdn.jsdelivr.net/npm/@etalab/decoupage-administratif@6.0.0/data/communes.json"
FICHES_LIST_URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/REF_STATION/liste_fiches_clim.geojson"
FICHE_URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/REF_STATION/FICHECLIM_{num}.data"

LAPSE_RATE = 0.0065  # °C/m
R_EARTH = 6371.0088


# --------------------------------------------------------------------------- utilitaires
def cog_mapper():
    """f(code) -> code commune actuel (anciens codes, déléguées/associées, arrondissements)."""
    data = json.loads(download(COG_URL, "decoupage_administratif_communes_6.0.0.json").read_text(encoding="utf-8"))
    current = {c["code"] for c in data if c["type"] == "commune-actuelle"}
    passage: dict[str, str] = {}
    for c in data:
        if c["type"] == "commune-actuelle":
            for old in c.get("anciensCodes", []) or []:
                passage.setdefault(old, c["code"])
    for c in data:
        if c["type"] in ("commune-deleguee", "commune-associee") and c["code"] not in current:
            passage.setdefault(c["code"], c.get("chefLieu"))

    def to_current(code):
        if code is None:
            return None
        code = str(code).strip()
        code = normalize_insee(code.zfill(5) if code.isdigit() else code)
        if code in current:
            return code
        code = passage.get(code)
        code = normalize_insee(code) if code else None
        return code if code in current else None

    return to_current


def read_xlsx(path: Path, sheet: int = 1) -> list[list]:
    """Lecteur xlsx minimal (stdlib) : liste de lignes (valeurs texte, colonnes A..)."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
    rows = []
    for r in ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml")).iter(ns + "row"):
        row = {}
        for c in r.findall(ns + "c"):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            idx = 0
            for ch in col:
                idx = idx * 26 + ord(ch) - 64
            v, t = c.find(ns + "v"), c.get("t")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(ns + "t"))
            else:
                val = v.text if v is not None else None
            row[idx - 1] = val
        rows.append([row.get(i) for i in range(max(row) + 1)] if row else [])
    return rows


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# --------------------------------------------------------------------------- altitude
def altitudes(df: pd.DataFrame) -> pd.Series:
    cache = {}
    if ALTI_CACHE.exists():
        cache = json.loads(ALTI_CACHE.read_text(encoding="utf-8"))
    key = lambda r: f"{r.lat:.5f},{r.lon:.5f}"  # noqa: E731
    todo = [r for r in df.itertuples() if key(r) not in cache]
    # lots géographiquement compacts (tri par latitude puis longitude)
    todo.sort(key=lambda r: (round(r.lat, 0), r.lon))
    batches = [todo[i:i + ALTI_BATCH] for i in range(0, len(todo), ALTI_BATCH)]
    if batches:
        print(f"  altimétrie IGN : {len(todo)} points, {len(batches)} requêtes", file=sys.stderr)
    session = requests.Session()
    session.headers["User-Agent"] = UA

    def fetch(batch):
        payload = {"lon": "|".join(f"{r.lon:.5f}" for r in batch), "lat": "|".join(f"{r.lat:.5f}" for r in batch),
                   "resource": ALTI_RESOURCE, "zonly": "true"}
        for attempt in range(5):
            try:
                resp = session.post(ALTI_URL, json=payload, timeout=60)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                elev = resp.json()["elevations"]
                if len(elev) != len(batch):
                    raise RuntimeError("réponse incomplète")
                return [(key(r), e) for r, e in zip(batch, elev)]
            except Exception as e:  # noqa: BLE001
                print(f"    altimétrie : tentative {attempt + 1} échouée ({e})", file=sys.stderr)
                time.sleep(2 ** attempt)
        return []

    done = 0
    with cf.ThreadPoolExecutor(ALTI_WORKERS) as ex:
        for res in ex.map(fetch, batches):
            cache.update(res)
            done += 1
            if done % 200 == 0:
                ALTI_CACHE.write_text(json.dumps(cache), encoding="utf-8")
                print(f"    {done}/{len(batches)}", file=sys.stderr)
    ALTI_CACHE.write_text(json.dumps(cache), encoding="utf-8")

    vals = [cache.get(key(r)) for r in df.itertuples()]
    s = pd.Series(vals, index=df.index, dtype=float)
    return s.where(s > -1000)  # -99999 = pas de donnée


# --------------------------------------------------------------------------- littoral
def loi_littoral(df: pd.DataFrame, to_current) -> pd.Series:
    rows = read_xlsx(download(LITTORAL_URL, "ot_loi_littoral_com2026.xlsx", max_age_days=90))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "codgeo")
    out = {}
    for r in rows[hdr + 1:]:
        if not r or not r[0] or len(r) < 3 or r[2] not in ("C", "NC"):
            continue
        code = to_current(r[0])
        if code:
            out[code] = max(out.get(code, 0), 1 if r[2] == "C" else 0)
    return pd.Series(out, dtype=float).reindex(df.index)


# --------------------------------------------------------------------------- distance à la mer
def distance_mer(df: pd.DataFrame) -> pd.Series:
    gj = json.loads(download(COAST_URL, "ne_10m_coastline_v5.1.2.geojson", max_age_days=3650).read_text())
    segs = []
    for f in gj["features"]:
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            a = np.asarray(line, dtype=float)
            if len(a) >= 2:
                segs.append(np.c_[a[:-1], a[1:]])
    S = np.vstack(segs)  # lon1, lat1, lon2, lat2
    out = pd.Series(np.nan, index=df.index)
    ok = df["lat"].notna() & df["lon"].notna()
    # un groupe par territoire (métropole, chaque DROM/COM), segments dans l'emprise ± 7°
    groups = np.where(df["dep"].str.len() == 3, df["dep"], "metro")
    for grp in np.unique(groups[ok.to_numpy()]):
        m = ok.to_numpy() & (groups == grp)
        lat, lon = df["lat"].to_numpy()[m], df["lon"].to_numpy()[m]
        pad = 7.0
        sel = ((np.maximum(S[:, 0], S[:, 2]) >= lon.min() - pad) & (np.minimum(S[:, 0], S[:, 2]) <= lon.max() + pad)
               & (np.maximum(S[:, 1], S[:, 3]) >= lat.min() - pad) & (np.minimum(S[:, 1], S[:, 3]) <= lat.max() + pad))
        s = S[sel]
        if not len(s):
            continue
        res = np.empty(len(lat))
        for i in range(0, len(lat), 256):
            la, lo = lat[i:i + 256, None], lon[i:i + 256, None]
            k = np.cos(np.radians(la))  # projection équirectangulaire locale centrée sur la commune
            ax, ay = (s[None, :, 0] - lo) * k, s[None, :, 1] - la
            bx, by = (s[None, :, 2] - lo) * k, s[None, :, 3] - la
            dx, dy = bx - ax, by - ay
            t = np.clip(-(ax * dx + ay * dy) / np.maximum(dx * dx + dy * dy, 1e-18), 0, 1)
            px, py = ax + t * dx, ay + t * dy
            j = np.argmin(px * px + py * py, axis=1)
            rows = np.arange(len(j))
            plon = lo[:, 0] + px[rows, j] / k[:, 0]
            plat = la[:, 0] + py[rows, j]
            res[i:i + 256] = haversine(la[:, 0], lo[:, 0], plat, plon)
        out[df.index[m]] = res
    return out


# --------------------------------------------------------------------------- montagne
def zone_montagne(df: pd.DataFrame, to_current) -> pd.Series:
    path = download(MONTAGNE_URL, "dgaln_loi_montagne_cog2022.xlsx", max_age_days=3650)
    codes = set()
    for sheet, col_names in ((2, ("INSEE_COM",)), (3, ("INSEE_COM", "OLD_INSEE"))):
        rows = read_xlsx(path, sheet)
        hdr = next(i for i, r in enumerate(rows) if r and "INSEE_COM" in r)
        cols = [rows[hdr].index(c) for c in col_names]
        for r in rows[hdr + 1:]:
            for c in cols:
                if len(r) > c and r[c]:
                    raw = str(r[c]).strip().zfill(5)
                    codes.add(raw)  # code direct (communes rétablies après défusion)
                    cur = to_current(raw)
                    if cur:
                        codes.add(cur)
    s = pd.Series(df.index.isin(list(codes)).astype(float), index=df.index)
    # liste limitée à la métropole : inconnu outre-mer
    s[df["dep"].str.len() == 3] = np.nan
    return s


# --------------------------------------------------------------------------- climat
SERIES = {
    "temp": "Température moyenne (Moyenne en °C)",
    "precip": "Précipitations : Hauteur moyenne mensuelle (mm)",
    "soleil": "Durée d'insolation (Moyenne en heures)",
}


def parse_fiche(text: str) -> dict:
    lines = text.splitlines()
    out = {}
    m = re.search(r"alt\s*:\s*(-?\d+)\s*m", text)
    out["alt"] = float(m.group(1)) if m else np.nan
    for key, title in SERIES.items():
        for i, line in enumerate(lines):
            if line.strip().rstrip(";").strip() == title:
                period = re.search(r"(\d{4})-(\d{4})", lines[i + 1])
                vals = [v.strip() for v in lines[i + 2].split(";")]
                nums = [v for v in vals[1:] if v != ""]
                try:
                    year = float(nums[12])
                except (IndexError, ValueError):
                    break
                monthly_ok = all(v not in (".", "") for v in nums[:12])
                if monthly_ok:
                    out[key] = year
                    out[key + "_period"] = period.group(0) if period else ""
                break
    return out


def climat_stations() -> pd.DataFrame:
    gj = json.loads(download(FICHES_LIST_URL, "meteofrance_liste_fiches_clim.geojson", max_age_days=90).read_text())
    stations = [(f["properties"]["num"], *f["geometry"]["coordinates"][:2]) for f in gj["features"]]

    def one(st):
        num, lon, lat = st
        try:
            p = download(FICHE_URL.format(num=num), f"ficheclim/FICHECLIM_{num}.data", max_age_days=365)
        except RuntimeError:
            return None
        rec = parse_fiche(p.read_bytes().decode("utf-8", errors="replace"))
        rec.update(num=num, lat=lat, lon=lon)
        return rec

    (CACHE / "ficheclim").mkdir(exist_ok=True)
    with cf.ThreadPoolExecutor(6) as ex:
        recs = [r for r in ex.map(one, stations) if r]
    st = pd.DataFrame(recs)
    print(f"  stations Météo-France : {len(st)} fiches, temp {st['temp'].notna().sum()}, "
          f"précip {st['precip'].notna().sum()}, insolation {st['soleil'].notna().sum()}", file=sys.stderr)
    return st


def idw(df, st, col, k=3, max_km=60.0, alt_correct=False):
    s = st[st[col].notna() & st["lat"].notna()]
    if alt_correct:
        s = s[s["alt"].notna()]
    slat, slon, sval = s["lat"].to_numpy(), s["lon"].to_numpy(), s[col].to_numpy()
    salt = s["alt"].to_numpy()
    out = np.full(len(df), np.nan)
    lat, lon, alt = df["lat"].to_numpy(), df["lon"].to_numpy(), df["altitude"].to_numpy()
    for i in range(0, len(df), 512):
        d = haversine(lat[i:i + 512, None], lon[i:i + 512, None], slat[None, :], slon[None, :])
        idx = np.argsort(d, axis=1)[:, :k]
        dk = np.take_along_axis(d, idx, axis=1)
        v = sval[idx]
        if alt_correct:
            a = alt[i:i + 512, None]
            v = np.where(np.isnan(a), v, v - LAPSE_RATE * (a - salt[idx]))
        w = 1.0 / np.maximum(dk, 1.0) ** 2
        w = np.where(dk <= max_km, w, 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[i:i + 512] = (w * v).sum(1) / w.sum(1)
    return out


# --------------------------------------------------------------------------- main
def main():
    to_current = cog_mapper()
    df = pd.DataFrame(load_communes()).set_index("code_insee")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

    df["altitude"] = altitudes(df)
    df["commune_littorale"] = loi_littoral(df, to_current)
    df["distance_mer_km"] = distance_mer(df)
    df["zone_montagne"] = zone_montagne(df, to_current)

    st = climat_stations()
    df["temp_moy"] = idw(df, st, "temp", k=3, max_km=60, alt_correct=True)
    df["precip_mm"] = idw(df, st, "precip", k=3, max_km=60)
    df["ensoleillement_h"] = idw(df, st, "soleil", k=3, max_km=150)

    cols = ["altitude", "commune_littorale", "distance_mer_km", "zone_montagne",
            "temp_moy", "precip_mm", "ensoleillement_h"]
    rnd = {"altitude": 0, "commune_littorale": 0, "distance_mer_km": 1, "zone_montagne": 0,
           "temp_moy": 1, "precip_mm": 0, "ensoleillement_h": 0}
    rows = []
    for code, r in df[cols].iterrows():
        row = {"code_insee": code}
        for c in cols:
            v = r[c]
            row[c] = None if pd.isna(v) else (int(round(v)) if rnd[c] == 0 else round(float(v), rnd[c]))
        rows.append(row)
    write_csv("geographie", rows, cols)

    periods = sorted(set(st["temp_period"].dropna()) | set(st["precip_period"].dropna()))
    mf = ("Météo-France – fiches climatologiques des stations (statistiques 1991-2020, ou période plus courte "
          "pour les stations récentes), édition " + time.strftime("%m/%Y"))
    G = "Cadre de vie"
    write_indicators("geographie", [
        {"id": "altitude", "label": "Altitude", "group": G, "unit": "m", "type": "number", "better": None,
         "description": "Altitude du centre de la commune (point central de geo.api.gouv.fr), lue sur le modèle "
                        "numérique de terrain RGE ALTI de l'IGN (MNT mondial hors couverture).",
         "source": "IGN – Géoplateforme, service d'altimétrie (RGE ALTI®), " + time.strftime("%Y"),
         "decimals": 0},
        {"id": "commune_littorale", "label": "Commune littorale", "group": G, "unit": "", "type": "boolean",
         "better": None,
         "description": "1 si la commune est soumise (en tout ou partie) à la loi Littoral du 3 janvier 1986 : "
                        "riveraine de la mer, d'un estuaire, d'un étang salé ou d'un plan d'eau intérieur de plus "
                        "de 1 000 ha (ex. lac d'Annecy).",
         "source": "DGALN-SIDAUH via Observatoire des territoires (ANCT) – classement loi Littoral, COG 2026",
         "decimals": 0},
        {"id": "distance_mer_km", "label": "Distance à la mer", "group": G, "unit": "km", "type": "number",
         "better": "lower",
         "description": "Distance à vol d'oiseau entre le centre de la commune et le trait de côte maritime le plus "
                        "proche (lacs exclus ; estuaires inclus jusqu'à la limite retenue par Natural Earth). "
                        "Trait de côte au 1:10 000 000 : précision de l'ordre du kilomètre, 0 à quelques km pour les "
                        "communes côtières.",
         "source": "Natural Earth 10m coastline v5.1.2 (domaine public) ; calcul pipeline",
         "decimals": 1, "scale": "log"},
        {"id": "zone_montagne", "label": "Zone de montagne", "group": G, "unit": "", "type": "boolean",
         "better": None,
         "description": "1 si la commune est classée (en tout ou partie) en zone de montagne au titre de la loi "
                        "Montagne du 9 janvier 1985. Métropole uniquement (vide outre-mer). Les communes nouvelles "
                        "créées depuis 2022 sont classées si l'une de leurs anciennes communes l'était.",
         "source": "DGALN-SIDAUH – communes de la loi Montagne au COG 2022 (data.gouv.fr)", "decimals": 0},
        {"id": "temp_moy", "label": "Température moyenne", "group": G, "unit": "°C", "type": "number",
         "better": None,
         "description": "Température moyenne annuelle (normale) estimée au centre de la commune : moyenne pondérée "
                        "(inverse du carré de la distance) des 3 stations Météo-France les plus proches dans un "
                        "rayon de 60 km, ramenées à l'altitude de la commune (gradient −6,5 °C/km).",
         "source": mf, "decimals": 1},
        {"id": "precip_mm", "label": "Précipitations annuelles", "group": G, "unit": "mm", "type": "number",
         "better": None,
         "description": "Cumul annuel moyen de précipitations (normale) : moyenne pondérée (inverse du carré de la "
                        "distance) des 3 stations Météo-France les plus proches dans un rayon de 60 km.",
         "source": mf, "decimals": 0},
        {"id": "ensoleillement_h", "label": "Ensoleillement annuel", "group": G, "unit": "h/an", "type": "number",
         "better": "higher",
         "description": "Durée annuelle moyenne d'insolation : moyenne pondérée (inverse du carré de la distance) "
                        "des 3 stations Météo-France mesurant l'insolation les plus proches dans un rayon de "
                        "150 km (réseau peu dense : valeur régionale indicative).",
         "source": mf, "decimals": 0},
    ])
    print(f"  périodes de stats climatiques rencontrées : {periods[:8]}{'…' if len(periods) > 8 else ''}",
          file=sys.stderr)

    n = len(df)
    for c in cols:
        nn = df[c].notna().sum()
        print(f"  {c:20s} {nn / n:6.1%}  moy={df[c].mean():.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
