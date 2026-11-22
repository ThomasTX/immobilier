"""Location courte durée (type Airbnb) par commune, d'après Inside Airbnb (CC BY 4.0) :
nombre d'annonces actives, prix par nuit, taux d'occupation et revenu annuel estimés,
rendement brut indicatif d'un T2.

- Zones : toutes les zones françaises de la page « Get the data » d'Inside Airbnb
  (extraction la plus récente par zone ; repli sur une liste connue si la page est inaccessible).
- Rattachement des annonces aux communes par point-dans-polygone sur les contours communaux
  (pipeline/.cache/communes_rounded.geojson, produit par build_geometry.py), index par bbox.
- Occupation : modèle « San Francisco » d'Inside Airbnb (voir les descriptions des indicateurs).
- Rendement : revenu estimé / (prix médian au m² des appartements DVF × 45 m²), lu dans
  pipeline/out/immobilier.csv.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CACHE, OUT, ROOT, download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

NAME = "location_courte"
G = "Location courte durée"
GEOJSON = CACHE / "communes_rounded.geojson"

IAB_PAGE = "https://insideairbnb.com/get-the-data/"
IAB_RE = re.compile(r"https?://data\.insideairbnb\.com/france/([^/\"'<>\s]+)/([^/\"'<>\s]+)/(\d{4}-\d{2}-\d{2})/data/listings\.csv\.gz")
# Repli si la page n'est pas accessible (zones et dates relevées en novembre 2026)
IAB_FALLBACK = [
    ("ile-de-france", "paris", "2026-09-16"),
    ("auvergne-rhone-alpes", "lyon", "2026-09-17"),
    ("nouvelle-aquitaine", "bordeaux", "2026-09-17"),
    ("pyrénées-atlantiques", "pays-basque", "2026-09-19"),
]
IAB_LICENCE = "Inside Airbnb (insideairbnb.com), licence CC BY 4.0"
REVIEW_RATE = 0.5        # part des séjours qui laissent un avis (modèle « San Francisco »)
AVG_STAY = 3             # durée moyenne de séjour (nuits), remplacée par minimum_nights si plus grand
OCC_CAP = 0.70           # plafond d'occupation
STR_MAX_MIN_NIGHTS = 30  # courte durée = séjour minimum < 30 nuits (définition Inside Airbnb)
PRICE_MIN, PRICE_MAX = 10, 5000
MIN_ACTIVE = 10          # annonces actives minimum pour publier une commune
MIN_VALUES = 5           # valeurs minimum pour une médiane (prix, revenu, occupation)
STR_ROOM_TYPES = ("Entire home/apt", "Private room", "Shared room")
T2_M2 = 45

COLUMNS = ["str_nb_annonces", "str_annonces_1000_hab", "str_prix_nuit", "str_occupation_pct",
           "str_revenu_annuel", "str_rendement_brut_pct"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def fr_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm_name(s)).strip("-")


def parse_price(v) -> float | None:
    """'$1,234.00' / '€1,234.00' (format anglo-saxon d'Inside Airbnb, devise locale) -> 1234.0"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = re.sub(r"[^0-9.]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


# ============================================================== Géométrie (point-dans-polygone)
class CommuneIndex:
    """Contours communaux + index par bbox, test point-dans-polygone vectorisé (numpy, pair-impair)."""

    def __init__(self, path: Path):
        gj = json.loads(path.read_text(encoding="utf-8"))
        self.codes: list[str] = []
        self.geoms: list = []
        bb = []
        for f in gj["features"]:
            g = f.get("geometry") or {}
            polys = g.get("coordinates") or []
            if g.get("type") == "Polygon":
                polys = [polys]
            elif g.get("type") != "MultiPolygon":
                continue
            xs = [p[0] for poly in polys for ring in poly for p in ring]
            ys = [p[1] for poly in polys for ring in poly for p in ring]
            if not xs:
                continue
            self.codes.append(normalize_insee(f["properties"]["c"]))
            self.geoms.append(polys)
            bb.append((min(xs), min(ys), max(xs), max(ys)))
        self.bbox = np.array(bb, dtype=float)
        self._edges: dict[int, tuple] = {}

    def edges(self, i: int):
        e = self._edges.get(i)
        if e is None:
            x1, y1, x2, y2 = [], [], [], []
            for poly in self.geoms[i]:
                for ring in poly:
                    r = np.asarray(ring, dtype=float)
                    if len(r) < 3:
                        continue
                    if (r[0] != r[-1]).any():
                        r = np.vstack([r, r[:1]])
                    x1.append(r[:-1, 0]); y1.append(r[:-1, 1]); x2.append(r[1:, 0]); y2.append(r[1:, 1])
            e = tuple(np.concatenate(a) for a in (x1, y1, x2, y2))
            self._edges[i] = e
        return e

    def _inside(self, i: int, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        x1, y1, x2, y2 = self.edges(i)
        out = np.zeros(len(px), dtype=bool)
        step = max(1, int(4_000_000 // max(len(x1), 1)))
        dy = np.where(y2 == y1, 1e-30, y2 - y1)
        for s in range(0, len(px), step):
            X = px[s:s + step, None]
            Y = py[s:s + step, None]
            cross = ((y1 > Y) != (y2 > Y)) & (X < (x2 - x1) * (Y - y1) / dy + x1)
            out[s:s + step] = (cross.sum(axis=1) % 2) == 1
        return out

    def locate(self, lon, lat) -> np.ndarray:
        """Code INSEE (normalisé) de la commune contenant chaque point, '' si aucune."""
        px = np.asarray(lon, dtype=float)
        py = np.asarray(lat, dtype=float)
        res = np.full(len(px), "", dtype=object)
        ok = np.isfinite(px) & np.isfinite(py)
        if not ok.any():
            return res
        xmin, xmax, ymin, ymax = px[ok].min(), px[ok].max(), py[ok].min(), py[ok].max()
        b = self.bbox
        cand = np.nonzero((b[:, 0] <= xmax) & (b[:, 2] >= xmin) & (b[:, 1] <= ymax) & (b[:, 3] >= ymin))[0]
        todo = ok.copy()
        for i in cand:
            m = todo & (px >= b[i, 0]) & (px <= b[i, 2]) & (py >= b[i, 1]) & (py <= b[i, 3])
            idx = np.nonzero(m)[0]
            if not len(idx):
                continue
            ins = self._inside(i, px[idx], py[idx])
            hit = idx[ins]
            res[hit] = self.codes[i]
            todo[hit] = False
        return res


def load_index() -> CommuneIndex:
    if not GEOJSON.exists():
        log("  contours absents : lancement de pipeline/build_geometry.py")
        subprocess.run([sys.executable, str(ROOT / "build_geometry.py")], check=False)
    if not GEOJSON.exists():
        raise RuntimeError(f"{GEOJSON} introuvable (build_geometry.py a échoué)")
    return CommuneIndex(GEOJSON)


# ============================================================== Inside Airbnb
def iab_zones() -> list[tuple[str, str, str]]:
    try:
        html = download(IAB_PAGE, "insideairbnb_get_the_data.html", max_age_days=7).read_text(encoding="utf-8",
                                                                                             errors="replace")
        latest: dict[tuple[str, str], str] = {}
        for m in IAB_RE.finditer(html):
            reg, city, d = (urllib.parse.unquote(m.group(1)), urllib.parse.unquote(m.group(2)), m.group(3))
            if d > latest.get((reg, city), ""):
                latest[(reg, city)] = d
        if latest:
            return sorted((r, c, d) for (r, c), d in latest.items())
        log("  ! aucune zone France trouvée sur la page Inside Airbnb, repli sur la liste connue")
    except Exception as e:  # noqa: BLE001
        log(f"  ! page Inside Airbnb inaccessible ({e}), repli sur la liste connue")
    return list(IAB_FALLBACK)


def iab_read(region: str, city: str, d: str) -> pd.DataFrame | None:
    path = f"/france/{region}/{city}/{d}/data/listings.csv.gz"
    url = "https://data.insideairbnb.com" + urllib.parse.quote(path)
    name = f"insideairbnb_{slug(city)}_{d}_listings.csv.gz"
    try:
        p = download(url, name, max_age_days=3650)  # URL datée : contenu immuable
    except Exception as e:  # noqa: BLE001
        log(f"  ! {city} ({d}) inaccessible : {e}")
        return None
    for old in CACHE.glob(f"insideairbnb_{slug(city)}_*_listings.csv.gz"):  # extractions périmées
        if old.name != name:
            old.unlink(missing_ok=True)
    want = ["id", "latitude", "longitude", "room_type", "price", "minimum_nights", "number_of_reviews_ltm",
            "last_review", "last_scraped"]
    try:
        df = pd.read_csv(p, usecols=lambda c: c in want, low_memory=False, dtype={"id": str, "price": str})
    except Exception as e:  # noqa: BLE001
        log(f"  ! {city} ({d}) illisible : {e}")
        return None
    for c in want:
        if c not in df:
            df[c] = np.nan
    df["zone"] = city
    df["extract_date"] = d
    return df


def inside_airbnb(index: CommuneIndex) -> tuple[dict[str, dict], list[tuple[str, str, int, int]]]:
    zones = iab_zones()
    log(f"  Inside Airbnb : {len(zones)} zone(s) France : " + ", ".join(f"{c} {d}" for _, c, d in zones))
    frames, used = [], []
    for reg, city, d in zones:
        df = iab_read(reg, city, d)
        if df is None or not len(df):
            continue
        df["code"] = index.locate(pd.to_numeric(df["longitude"], errors="coerce"),
                                  pd.to_numeric(df["latitude"], errors="coerce"))
        n_out = int((df["code"] == "").sum())
        log(f"    {city} {d} : {len(df)} annonces, {n_out} hors contours communaux")
        frames.append(df[df["code"] != ""])
        used.append((city, d, len(df), int(df["code"].nunique())))
    if not frames:
        return {}, used
    df = pd.concat(frames, ignore_index=True).drop_duplicates("id", keep="first")

    ref = pd.to_datetime(df["extract_date"])
    scraped = pd.to_datetime(df["last_scraped"], errors="coerce").fillna(ref)
    last_rev = pd.to_datetime(df["last_review"], errors="coerce")
    ltm = pd.to_numeric(df["number_of_reviews_ltm"], errors="coerce").fillna(0)
    minn = pd.to_numeric(df["minimum_nights"], errors="coerce").fillna(1).clip(lower=1)
    price = df["price"].map(parse_price).astype(float)
    price = price.where(price.between(PRICE_MIN, PRICE_MAX))

    active = (ltm > 0) | (last_rev >= scraped - pd.Timedelta(days=365))
    short = minn < STR_MAX_MIN_NIGHTS
    str_ok = active & short & df["room_type"].isin(STR_ROOM_TYPES)
    entire = df["room_type"] == "Entire home/apt"

    nights = (ltm / REVIEW_RATE * np.maximum(AVG_STAY, minn)).clip(upper=OCC_CAP * 365)
    df = df.assign(active=str_ok, entire=entire & str_ok, occ=nights / 365 * 100, price_eur=price,
                   revenue=price * nights)

    out: dict[str, dict] = {}
    for code, g in df[df["active"]].groupby("code"):
        n = len(g)
        if n < MIN_ACTIVE:
            continue
        e = g[g["entire"]]
        sub = e if len(e) >= MIN_VALUES else g  # logements entiers de préférence
        prices = e["price_eur"].dropna()
        revs = e["revenue"].dropna()
        out[code] = {
            "str_nb_annonces": n,
            "str_prix_nuit": float(prices.median()) if len(prices) >= MIN_VALUES else None,
            "str_occupation_pct": float(sub["occ"].median()) if len(sub) >= MIN_VALUES else None,
            "str_revenu_annuel": float(revs.median()) if len(revs) >= MIN_VALUES else None,
        }
    return out, used


# ============================================================== main
def main():
    communes = load_communes()
    base = [c["code_insee"] for c in communes]
    pop = {}
    for c in communes:
        try:
            pop[c["code_insee"]] = float(c.get("population") or "nan")
        except ValueError:
            pass

    stats, used = inside_airbnb(load_index())

    prix_appart: dict[str, float] = {}
    imm = OUT / "immobilier.csv"
    if imm.exists():
        d = pd.read_csv(imm, dtype={"code_insee": str}, usecols=["code_insee", "prix_m2_appart"])
        prix_appart = dict(zip(d["code_insee"], d["prix_m2_appart"]))
    else:
        log("  ! pipeline/out/immobilier.csv absent : rendement non calculé (lancer sources/immobilier.py)")

    rows = []
    for code in base:
        row = {"code_insee": code}
        rec = stats.get(code)
        if rec:
            nb = rec["str_nb_annonces"]
            row["str_nb_annonces"] = int(nb)
            p = pop.get(code)
            row["str_annonces_1000_hab"] = round(nb / p * 1000, 2) if p and p == p and p > 0 else None
            for c in ("str_prix_nuit", "str_revenu_annuel"):
                row[c] = int(round(rec[c])) if rec[c] is not None else None
            row["str_occupation_pct"] = round(rec["str_occupation_pct"], 1) if rec["str_occupation_pct"] is not None else None
            rev, pm2 = rec["str_revenu_annuel"], prix_appart.get(code)
            if rev is not None and pm2 is not None and pm2 == pm2 and pm2 > 0:
                r = rev / (pm2 * T2_M2) * 100
                row["str_rendement_brut_pct"] = round(r, 2) if 0 < r < 60 else None
        rows.append(row)
    log(f"  {len(stats)} communes renseignées")
    write_csv(NAME, rows, COLUMNS)

    ext = ", ".join(f"{city.replace('-', ' ').title()} (extraction du {fr_date(d)})" for city, d, _, _ in used)
    src = f"{IAB_LICENCE} ; zones : {ext}" if used else IAB_LICENCE
    zones_txt = ", ".join(city.replace("-", " ").title() for city, *_ in used) or "aucune"
    common_txt = (f"Couverture limitée aux zones publiées par Inside Airbnb pour la France ({zones_txt}) : "
                  "vide ailleurs. Annonces actives = au moins un avis dans les 12 mois précédant l'extraction, "
                  f"séjour minimum < {STR_MAX_MIN_NIGHTS} nuits, rattachées à la commune par leurs coordonnées "
                  f"(floutées d'environ 150 m par Airbnb) ; commune publiée à partir de {MIN_ACTIVE} annonces "
                  "actives. ")
    occ_model = (f"modèle « San Francisco » d'Inside Airbnb : nuits réservées = avis des 12 derniers mois "
                 f"/ {REVIEW_RATE:.0%} (part des séjours laissant un avis) × durée de séjour "
                 f"({AVG_STAY} nuits, ou le séjour minimum s'il est plus long), plafonné à {OCC_CAP:.0%} de l'année")
    regl = ("Indicatif : brut, avant charges, ménage, frais de plateforme, fiscalité et vacance ; la location "
            "courte durée est encadrée localement (autorisation de changement d'usage, compensation à Paris et "
            "dans d'autres grandes villes, quotas, plafond de 120 nuits/an — 90 dans certaines communes — pour "
            "une résidence principale).")
    ind = [
        {"id": "str_nb_annonces", "label": "Annonces de location courte durée", "group": G, "unit": "annonces",
         "type": "number", "better": None, "decimals": 0, "scale": "log", "source": src,
         "description": "Nombre d'annonces Airbnb actives (logements entiers, chambres privées et partagées ; "
                        "hors chambres d'hôtel). " + common_txt},
        {"id": "str_annonces_1000_hab", "label": "Annonces courte durée pour 1 000 habitants", "group": G,
         "unit": "‰ hab.", "type": "number", "better": None, "decimals": 1, "scale": "log",
         "source": src + " ; population municipale INSEE",
         "description": "Annonces Airbnb actives pour 1 000 habitants (population municipale) : indicateur de "
                        "pression touristique sur le parc de logements. " + common_txt},
        {"id": "str_prix_nuit", "label": "Prix par nuit (logement entier)", "group": G, "unit": "€/nuit",
         "type": "number", "better": "higher", "decimals": 0, "scale": "log", "source": src,
         "description": "Prix par nuit médian affiché des logements entiers actifs à la date d'extraction "
                        f"(hors frais de ménage et de service ; prix hors [{PRICE_MIN} ; {PRICE_MAX}] € écartés ; "
                        f"vide si moins de {MIN_VALUES} prix connus). " + common_txt},
        {"id": "str_occupation_pct", "label": "Taux d'occupation estimé (courte durée)", "group": G, "unit": "%",
         "type": "number", "better": "higher", "decimals": 0, "source": src,
         "description": f"Occupation médiane estimée des logements entiers actifs (toutes annonces actives si moins "
                        f"de {MIN_VALUES} logements entiers), {occ_model}. Estimation grossière : ignore les "
                        "réservations sans avis, les autres plateformes et les périodes bloquées par l'hôte. "
                        + common_txt},
        {"id": "str_revenu_annuel", "label": "Revenu annuel brut estimé (courte durée)", "group": G, "unit": "€/an",
         "type": "number", "better": "higher", "decimals": 0, "scale": "log", "source": src,
         "description": "Revenu annuel brut médian estimé des logements entiers actifs = prix par nuit affiché × "
                        f"nuits réservées estimées ({occ_model}). Vide si moins de {MIN_VALUES} logements entiers "
                        "avec prix. " + common_txt + regl},
        {"id": "str_rendement_brut_pct", "label": "Rendement brut courte durée (T2, indicatif)", "group": G,
         "unit": "%", "type": "number", "better": "higher", "decimals": 1,
         "source": src + " ; DVF (DGFiP) via « Statistiques DVF » Etalab (prix médian au m² des appartements)",
         "description": f"Rendement brut indicatif d'un T2 de {T2_M2} m² loué en courte durée = revenu annuel brut "
                        f"estimé / (prix médian au m² des appartements × {T2_M2} m²) × 100. Le revenu est celui d'un "
                        "logement entier médian de la commune (toutes tailles), pas spécifiquement d'un T2. "
                        + regl},
    ]
    write_indicators(NAME, ind)


if __name__ == "__main__":
    main()
