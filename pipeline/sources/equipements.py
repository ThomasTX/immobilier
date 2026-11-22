"""Équipements et services de proximité par commune (INSEE, Base Permanente des Équipements).

Source : BPE, fichier détail géolocalisé (millésime le plus récent publié par l'INSEE).
Pour chaque commune :
  - nb_<type> : nombre d'équipements du type implantés dans la commune ;
  - dist_<type>_km : distance à vol d'oiseau entre le centre de la commune
    (communes.csv) et l'équipement du type le plus proche, où qu'il soit.

Coordonnées : LONGITUDE/LATITUDE (WGS84) fournies par la BPE ; à défaut, conversion
Lambert 93 -> WGS84 (EPSG:2154, métropole) ; à défaut, centre de la commune d'implantation.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

BPE_YEAR = 2025
BPE_URL = f"https://www.insee.fr/fr/statistiques/fichier/8217525/BPE{BPE_YEAR % 100}.zip"
SOURCE = f"INSEE, Base Permanente des Équipements (BPE) {BPE_YEAR}, fichier détail géolocalisé"
R_EARTH = 6371.0088  # km

# --------------------------------------------------------------------------- nomenclature
# id -> (codes TYPEQU, libellé, description). Un code "F1" (2 caractères) = tout le sous-domaine.
COUNTS: dict[str, tuple[list[str], str, str]] = {
    "nb_ecoles_maternelles": (["C107", "C108"], "Écoles maternelles",
                              "Écoles maternelles et écoles primaires (qui comportent des classes maternelles)."),
    "nb_ecoles_elementaires": (["C108", "C109"], "Écoles élémentaires",
                               "Écoles élémentaires et écoles primaires (qui comportent des classes élémentaires)."),
    "nb_colleges": (["C201"], "Collèges", "Collèges publics et privés."),
    "nb_lycees": (["C301", "C302", "C303"], "Lycées",
                  "Lycées d'enseignement général et/ou technologique, professionnels et agricoles."),
    "nb_creches": (["D502", "D509"], "Crèches",
                   "Établissements d'accueil du jeune enfant (crèches, multi-accueils) et micro-crèches."),
    "nb_medecins_generalistes": (["D265"], "Médecins généralistes", "Médecins généralistes (libéraux)."),
    "nb_dentistes": (["D277"], "Chirurgiens-dentistes", "Chirurgiens-dentistes."),
    "nb_pharmacies": (["D307"], "Pharmacies", "Pharmacies d'officine."),
    "urgences": (["D106"], "Service d'urgences", "Présence d'au moins un service d'urgences dans la commune."),
    "nb_maternites": (["D107"], "Maternités", "Maternités (établissements autorisés en obstétrique)."),
    "nb_supermarches": (["B104", "B105"], "Supermarchés et hypermarchés",
                        "Supermarchés, magasins multi-commerces, hypermarchés et grands magasins."),
    "nb_superettes_epiceries": (["B201", "B202"], "Supérettes et épiceries", "Supérettes et épiceries."),
    "nb_boulangeries": (["B207"], "Boulangeries", "Boulangeries-pâtisseries."),
    "nb_bureaux_poste": (["A206", "A207", "A208"], "Points de contact postaux",
                         "Bureaux de poste, agences postales communales et relais poste."),
    "nb_banques": (["A203"], "Banques", "Agences bancaires et caisses d'épargne."),
    "nb_equipements_sport": (["F1"], "Équipements sportifs",
                             "Équipements sportifs (gymnases, terrains, piscines, courts de tennis, salles "
                             "spécialisées, etc. ; sous-domaine « F1 » de la BPE)."),
    "nb_cinemas": (["F303"], "Cinémas", "Cinémas."),
    "nb_bibliotheques": (["F307"], "Bibliothèques", "Bibliothèques et médiathèques."),
}
# id distance -> (indicateur de comptage dont on reprend les codes, libellé)
DISTANCES: dict[str, tuple[str, str, str]] = {
    "dist_ecole_maternelle_km": ("nb_ecoles_maternelles", "Distance à l'école maternelle", "école maternelle ou primaire"),
    "dist_ecole_elementaire_km": ("nb_ecoles_elementaires", "Distance à l'école élémentaire",
                                  "école élémentaire ou primaire"),
    "dist_college_km": ("nb_colleges", "Distance au collège", "collège"),
    "dist_lycee_km": ("nb_lycees", "Distance au lycée", "lycée (général, technologique, professionnel ou agricole)"),
    "dist_medecin_generaliste_km": ("nb_medecins_generalistes", "Distance au médecin généraliste", "médecin généraliste"),
    "dist_pharmacie_km": ("nb_pharmacies", "Distance à la pharmacie", "pharmacie"),
    "dist_urgences_km": ("urgences", "Distance aux urgences", "service d'urgences"),
    "dist_maternite_km": ("nb_maternites", "Distance à la maternité", "maternité"),
    "dist_supermarche_km": ("nb_supermarches", "Distance au supermarché", "supermarché ou hypermarché"),
    "dist_boulangerie_km": ("nb_boulangeries", "Distance à la boulangerie", "boulangerie-pâtisserie"),
    "dist_bureau_poste_km": ("nb_bureaux_poste", "Distance au point poste",
                             "bureau de poste, agence postale ou relais poste"),
}


def matches(typequ: pd.Series, codes: list[str]) -> pd.Series:
    full = [c for c in codes if len(c) == 4]
    m = typequ.isin(full)
    for p in (c for c in codes if len(c) < 4):
        m |= typequ.str.startswith(p)
    return m


# --------------------------------------------------------------------------- projections
def lambert93_to_wgs84(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lambert 93 (EPSG:2154, RGF93/GRS80) -> (lon, lat) en degrés (RGF93 ≈ WGS84 au mètre près)."""
    e = 0.0818191910428158  # excentricité GRS80
    n = 0.7256077650532670
    c = 11754255.426096
    xs, ys = 700000.0, 12655612.049876
    lon0 = np.radians(3.0)
    dx, dy = x - xs, y - ys
    r = np.hypot(dx, dy)
    gamma = np.arctan2(dx, -dy)
    lon = lon0 + gamma / n
    lat_iso = -np.log(r / c) / n
    phi = 2 * np.arctan(np.exp(lat_iso)) - np.pi / 2
    for _ in range(12):
        es = e * np.sin(phi)
        phi = 2 * np.arctan(((1 + es) / (1 - es)) ** (e / 2) * np.exp(lat_iso)) - np.pi / 2
    return np.degrees(lon), np.degrees(phi)


def unit_vectors(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


# --------------------------------------------------------------------------- plus proche voisin
def nearest_km(q_lat: np.ndarray, q_lon: np.ndarray, p_lat: np.ndarray, p_lon: np.ndarray,
               cell_deg: float = 0.5, margin_km: float = 30.0) -> np.ndarray:
    """Distance (km, grand cercle) de chaque point requête au point cible le plus proche.

    Requêtes regroupées par cellule de grille ; pour chaque cellule, seuls les points
    cibles de la boîte élargie de `margin_km` sont testés (produit scalaire de vecteurs
    unitaires). Si le meilleur candidat est à plus de `margin_km` (résultat non garanti)
    ou s'il n'y a aucun candidat, recherche exhaustive pour ces requêtes.
    """
    out = np.full(len(q_lat), np.nan)
    if len(p_lat) == 0:
        return out
    qv, pv = unit_vectors(q_lat, q_lon), unit_vectors(p_lat, p_lon)

    def solve(qi: np.ndarray, pi: np.ndarray) -> np.ndarray:
        best = np.empty(len(qi), dtype=np.int64)
        step = max(1, int(2e7 // max(len(pi), 1)))
        for s in range(0, len(qi), step):
            best[s:s + step] = pi[np.argmax(qv[qi[s:s + step]] @ pv[pi].T, axis=1)]
        chord = np.linalg.norm(qv[qi] - pv[best], axis=1)
        return 2 * R_EARTH * np.arcsin(np.clip(chord / 2, 0, 1))

    cy = np.floor(q_lat / cell_deg).astype(np.int64)
    cx = np.floor(q_lon / cell_deg).astype(np.int64)
    key = cy * 100000 + cx
    order = np.argsort(key, kind="stable")
    bounds = np.flatnonzero(np.diff(key[order])) + 1
    redo = []
    for grp in np.split(order, bounds):
        la0, la1 = q_lat[grp].min(), q_lat[grp].max()
        lo0, lo1 = q_lon[grp].min(), q_lon[grp].max()
        dlat = margin_km / 110.0
        coslat = np.cos(np.radians(min(89.0, max(abs(la0), abs(la1)) + dlat)))
        dlon = min(180.0, margin_km / (110.0 * coslat))
        cand = np.flatnonzero((p_lat >= la0 - dlat) & (p_lat <= la1 + dlat)
                              & (p_lon >= lo0 - dlon) & (p_lon <= lo1 + dlon))
        if len(cand) == 0:
            redo.append(grp)
            continue
        d = solve(grp, cand)
        ok = d <= margin_km
        out[grp[ok]] = d[ok]
        if not ok.all():
            redo.append(grp[~ok])
    if redo:
        qi = np.concatenate(redo)
        out[qi] = solve(qi, np.arange(len(p_lat)))
    return out


# --------------------------------------------------------------------------- lecture BPE
def load_bpe(centres: dict[str, tuple[float, float]]) -> pd.DataFrame:
    path = download(BPE_URL, f"insee_BPE{BPE_YEAR % 100}.zip", max_age_days=365)
    codes = sorted({c for v in COUNTS.values() for c in v[0]})
    full = {c for c in codes if len(c) == 4}
    prefixes = tuple(c for c in codes if len(c) < 4)
    cols = ["DEPCOM", "DEP", "TYPEQU", "LAMBERT_X", "LAMBERT_Y", "LONGITUDE", "LATITUDE", "EPSG"]
    with zipfile.ZipFile(path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        parts = []
        with z.open(member) as f:
            reader = pd.read_csv(f, sep=";", usecols=cols, chunksize=500_000, low_memory=False,
                                 dtype={"DEPCOM": str, "DEP": str, "TYPEQU": str, "EPSG": str})
            for ch in reader:
                keep = ch["TYPEQU"].isin(full) | ch["TYPEQU"].str.startswith(prefixes)
                parts.append(ch[keep])
                print(f"    … {sum(len(p) for p in parts):,} équipements retenus", file=sys.stderr, end="\r")
    print(file=sys.stderr)
    df = pd.concat(parts, ignore_index=True)
    all_deps = set(pd.concat([p["DEP"] for p in parts]).dropna().unique())
    df["code_insee"] = df["DEPCOM"].map(normalize_insee)
    for c in ("LAMBERT_X", "LAMBERT_Y", "LONGITUDE", "LATITUDE"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    lat, lon = df["LATITUDE"].to_numpy(float).copy(), df["LONGITUDE"].to_numpy(float).copy()
    # 1) repli Lambert 93 (métropole)
    m = np.isnan(lat) & (df["EPSG"].fillna("2154") == "2154").to_numpy() \
        & df["LAMBERT_X"].notna().to_numpy() & df["LAMBERT_Y"].notna().to_numpy()
    if m.any():
        lo, la = lambert93_to_wgs84(df.loc[m, "LAMBERT_X"].to_numpy(float), df.loc[m, "LAMBERT_Y"].to_numpy(float))
        lon[m], lat[m] = lo, la
    # 2) repli centre de la commune d'implantation
    m2 = np.isnan(lat)
    if m2.any():
        cc = df.loc[m2, "code_insee"].map(centres)
        lat[m2] = [v[0] if isinstance(v, tuple) else np.nan for v in cc]
        lon[m2] = [v[1] if isinstance(v, tuple) else np.nan for v in cc]
    df["lat"], df["lon"] = lat, lon
    df["geo"] = np.where(~np.isnan(df["LATITUDE"]), "bpe", np.where(m, "lambert93", np.where(m2, "centre", "")))
    df.attrs["deps"] = all_deps
    return df


def check_lambert(df: pd.DataFrame) -> None:
    """Contrôle de la conversion Lambert 93 sur les équipements ayant aussi des coordonnées GPS."""
    s = df[(df["EPSG"] == "2154") & df["LATITUDE"].notna() & df["LAMBERT_X"].notna()].head(20000)
    if len(s):
        lo, la = lambert93_to_wgs84(s["LAMBERT_X"].to_numpy(float), s["LAMBERT_Y"].to_numpy(float))
        err = np.hypot((la - s["LATITUDE"]) * 111.2, (lo - s["LONGITUDE"]) * 111.2 * np.cos(np.radians(la))) * 1000
        print(f"  contrôle Lambert93→WGS84 : écart médian {np.median(err):.1f} m, max {err.max():.1f} m",
              file=sys.stderr)


def main():
    communes = load_communes()
    codes = [c["code_insee"] for c in communes]
    c_lat = np.array([float(c["lat"]) for c in communes])
    c_lon = np.array([float(c["lon"]) for c in communes])
    centres = {c["code_insee"]: (float(c["lat"]), float(c["lon"])) for c in communes}

    bpe = load_bpe(centres)
    check_lambert(bpe)
    print(f"  géolocalisation : {bpe['geo'].value_counts().to_dict()}", file=sys.stderr)
    known = set(codes)
    unk = bpe.loc[~bpe["code_insee"].isin(known)]
    if len(unk):
        print(f"  ⚠ {len(unk)} équipements dans {unk['code_insee'].nunique()} codes communes absents du référentiel "
              f"(ex. {sorted(unk['code_insee'].unique())[:8]}) : exclus des comptages, conservés pour les distances",
              file=sys.stderr)

    # Communes couvertes par la BPE (métropole + DROM) ; ailleurs (COM, TAAF) -> vide
    deps = bpe.attrs["deps"]
    dep_of = {c["code_insee"]: c["dep"] for c in communes}
    covered = np.array([dep_of[c] in deps for c in codes])

    res = pd.DataFrame({"code_insee": codes})
    for ind, (tcodes, _, _) in COUNTS.items():
        sel = bpe[matches(bpe["TYPEQU"], tcodes)]
        cnt = sel.groupby("code_insee").size()
        v = res["code_insee"].map(cnt).fillna(0).astype(float)
        if ind == "urgences":
            v = (v > 0).astype(float)
        v[~covered] = np.nan
        res[ind] = v

    pts_all = bpe.dropna(subset=["lat", "lon"])
    for ind, (cnt_id, _, _) in DISTANCES.items():
        sel = pts_all[matches(pts_all["TYPEQU"], COUNTS[cnt_id][0])]
        pts = np.unique(np.round(sel[["lat", "lon"]].to_numpy(float), 5), axis=0)
        d = nearest_km(c_lat, c_lon, pts[:, 0], pts[:, 1])
        d[~covered] = np.nan
        res[ind] = np.round(d, 2)
        print(f"  {ind}: {len(pts):,} points distincts, médiane {np.nanmedian(d):.1f} km", file=sys.stderr)

    count_cols, dist_cols = list(COUNTS), list(DISTANCES)
    cols = count_cols + dist_cols
    for c in count_cols:
        res[c] = res[c].astype("Int64")
    rows = res.astype(object).where(res.notna(), None).to_dict("records")
    write_csv("equipements", rows, cols)

    inds = []
    for ind, (_, label, desc) in COUNTS.items():
        if ind == "urgences":
            inds.append({"id": ind, "label": label, "group": "Services", "unit": "", "type": "boolean",
                         "better": "higher", "description": desc.rstrip(".") + " (1 = oui, 0 = non).",
                         "source": SOURCE, "decimals": 0})
        else:
            inds.append({"id": ind, "label": label, "group": "Services", "unit": "équip.", "type": "number",
                         "better": "higher",
                         "description": f"Nombre d'équipements implantés dans la commune : {desc[0].lower() + desc[1:]}",
                         "source": SOURCE, "decimals": 0})
    for ind, (_, label, what) in DISTANCES.items():
        inds.append({"id": ind, "label": label, "group": "Services", "unit": "km", "type": "number",
                     "better": "lower",
                     "description": f"Distance à vol d'oiseau entre le point central de la commune (geo.api.gouv.fr) et l'équipement "
                                    f"le plus proche de type {what}, qu'il soit situé dans la commune ou dans une "
                                    f"autre. Calculée à partir des coordonnées géographiques des équipements : une "
                                    f"commune étendue peut avoir une distance non nulle même si elle en possède un.",
                     "source": SOURCE, "decimals": 1})
    write_indicators("equipements", inds)

    n = len(res)
    for c in cols:
        print(f"  {c}: {res[c].notna().sum() / n:.1%} non vides", file=sys.stderr)


if __name__ == "__main__":
    main()
