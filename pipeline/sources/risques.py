"""Risques naturels et technologiques par commune.

Sources :
  - Base GASPAR (Géorisques / DGPR), export complet https://files.georisques.fr/GASPAR/gaspar.zip :
      * ddrm_risq : risques recensés par commune (DDRM / porter à connaissance)
      * catnat    : arrêtés de reconnaissance de catastrophe naturelle (depuis 1982)
      * pprn / pprt / pprm : plans de prévention des risques (naturels, technologiques, miniers)
  - Zonage sismique réglementaire (décret 2010-1255, en vigueur depuis le 1er mai 2011),
    couche communale publiée sur data.gouv.fr (France_zonage_sismique, 2018)
  - Potentiel radon des communes (IRSN / ASN, arrêté du 27 juin 2018), data.gouv.fr
  - Table de passage des anciens codes communes (communes nouvelles, déléguées, associées) :
    @etalab/decoupage-administratif (COG le plus récent)
"""
import io
import json
import struct
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

GASPAR_URL = "https://files.georisques.fr/GASPAR/gaspar.zip"
SISMIQUE_URL = ("https://static.data.gouv.fr/resources/zonage-sismique-de-la-france-1/"
                "20180730-100725/France_zonage_sismique.zip")
RADON_URL = ("https://static.data.gouv.fr/resources/connaitre-le-potentiel-radon-de-ma-commune/"
             "20190506-174309/radon.csv")
COG_URL = "https://cdn.jsdelivr.net/npm/@etalab/decoupage-administratif@6.0.0/data/communes.json"

# PPR encore actifs (on ignore les procédures abrogées, annulées, déprescrites)
PPR_ETATS_ACTIFS = {"Opposable", "Prescrit"}


# --------------------------------------------------------------------------- COG
def cog_mapper():
    """Retourne f(code) -> code commune actuel (ou None) en gérant anciens codes,
    communes déléguées/associées et arrondissements municipaux."""
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

    def to_current(code) -> str | None:
        if code is None or (isinstance(code, float) and code != code):
            return None
        code = normalize_insee(str(code).strip().zfill(5) if str(code).strip().isdigit() else str(code).strip())
        if code in current:
            return code
        code = passage.get(code)
        if code is not None:
            code = normalize_insee(code)
        return code if code in current else None

    return to_current


# --------------------------------------------------------------------------- lecture
def read_dbf(raw: bytes) -> pd.DataFrame:
    """Lecteur DBF minimal (dBase III, champs caractère/numérique)."""
    n = struct.unpack("<I", raw[4:8])[0]
    hlen, rlen = struct.unpack("<HH", raw[8:12])
    fields, pos = [], 32
    while raw[pos] != 0x0D:
        name = raw[pos:pos + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, raw[pos + 16]))
        pos += 32
    rows = []
    for i in range(n):
        rec = raw[hlen + i * rlen: hlen + (i + 1) * rlen]
        if rec[:1] == b"*":
            continue
        off, row = 1, {}
        for name, length in fields:
            row[name] = rec[off:off + length].decode("latin-1").strip()
            off += length
        rows.append(row)
    return pd.DataFrame(rows)


def gaspar_tables() -> dict[str, pd.DataFrame]:
    z = zipfile.ZipFile(download(GASPAR_URL, "gaspar.zip", max_age_days=30))
    out = {}
    for name in z.namelist():
        key = name.split("_gaspar")[0]
        out[key] = pd.read_csv(io.BytesIO(z.read(name)), sep=";", dtype=str,
                               encoding="utf-8", encoding_errors="replace")
    out["_millesime"] = pd.DataFrame({"m": [z.namelist()[0].rsplit("_", 1)[1].split(".")[0]]})
    return out


def fill_nearest(df: pd.DataFrame, cols: list[str], max_km: float = 15.0) -> None:
    import numpy as np
    geo = pd.DataFrame(load_communes()).set_index("code_insee")[["lat", "lon"]].astype(float).reindex(df.index)
    lat, lon = np.radians(geo["lat"].to_numpy()), np.radians(geo["lon"].to_numpy())
    for col in cols:
        vals = df[col].to_numpy(dtype=float)
        known = ~np.isnan(vals) & ~np.isnan(lat)
        for i in np.where(np.isnan(vals) & ~np.isnan(lat))[0]:
            d = 2 * 6371 * np.arcsin(np.sqrt(np.sin((lat[known] - lat[i]) / 2) ** 2 + np.cos(lat[i]) * np.cos(
                lat[known]) * np.sin((lon[known] - lon[i]) / 2) ** 2))
            j = d.argmin()
            if d[j] <= max_km:
                df.iloc[i, df.columns.get_loc(col)] = vals[known][j]


# --------------------------------------------------------------------------- main
def main():
    to_current = cog_mapper()
    communes = [c["code_insee"] for c in load_communes()]
    base = pd.DataFrame(index=pd.Index(communes, name="code_insee"))

    g = gaspar_tables()
    millesime = g["_millesime"]["m"].iloc[0]  # ex. 2026-11-02
    print(f"  GASPAR du {millesime}", file=sys.stderr)

    # ---- Risques recensés (DDRM)
    ddrm = g["ddrm_risq"].copy()
    ddrm["code_insee"] = ddrm["cod_commune"].map(to_current)
    ddrm = ddrm.dropna(subset=["code_insee"])
    codes = ddrm.groupby("code_insee")["num_risque"].apply(lambda s: set(s.dropna()))

    def has(pred):
        return codes.map(lambda s: int(any(pred(c) for c in s)))

    mvt_sub = {"121", "122", "123", "124", "125", "126"}
    ddrm_flags = pd.DataFrame({
        "risque_inondation": has(lambda c: c.startswith("11")),
        # mouvements de terrain hors retrait-gonflement des argiles (127, indicateur dédié)
        "risque_mouvement_terrain": codes.map(
            lambda s: int(bool(s & mvt_sub) or ("12" in s and not any(c.startswith("12") and len(c) == 3 for c in s)))),
        "risque_retrait_argiles": has(lambda c: c == "127"),
        "risque_feux_foret": has(lambda c: c == "16"),
        "risque_avalanche": has(lambda c: c == "14"),
        "risque_submersion_marine": has(lambda c: c == "117"),
        "risque_industriel": has(lambda c: c.startswith("21")),
        "risque_nucleaire": has(lambda c: c == "22"),
        "risque_minier": has(lambda c: c[:2] in ("31", "32", "33", "34")),
        # nombre de familles de risques distinctes (codes à 2 chiffres : inondation, séisme, TMD…)
        "nb_risques": codes.map(lambda s: len({c[:2] if c[:1].isdigit() else c for c in s})),
    })
    df = base.join(ddrm_flags)

    # ---- Compléments par les PPR actifs (une commune couverte par un PPR est exposée)
    def ppr_communes(t: pd.DataFrame, mask) -> set:
        t = t[t["LIBELLE ETAT"].isin(PPR_ETATS_ACTIFS) & mask(t)]
        return set(t["CODE INSEE COMMUNE"].map(to_current).dropna())

    pprn, pprt, pprm = g["pprn"], g["pprt"], g["pprm"]
    r2, r3 = pprn["CODE RISQUE 2"].fillna(""), pprn["CODE RISQUE 3"].fillna("")
    extra = {
        "risque_inondation": ppr_communes(pprn, lambda t: r2 == "11"),
        "risque_submersion_marine": ppr_communes(pprn, lambda t: (r3 == "117") | (t["CODE MODELE"] == "PPRN-L") & (r3 != "126") & (r3 != "125")),
        "risque_mouvement_terrain": ppr_communes(pprn, lambda t: (r2 == "12") & (r3 != "127") & (t["CODE MODELE"] != "PPRN-RGA")),
        "risque_retrait_argiles": ppr_communes(pprn, lambda t: (r3 == "127") | (t["CODE MODELE"] == "PPRN-RGA")),
        "risque_avalanche": ppr_communes(pprn, lambda t: r2 == "14"),
        "risque_feux_foret": ppr_communes(pprn, lambda t: r2 == "16"),
        "risque_industriel": ppr_communes(pprt, lambda t: t["CODE RISQUE 2"].fillna("") == "21"),
        "risque_minier": ppr_communes(pprm, lambda t: t["CODE RISQUE 1"].fillna("") == "30"),
    }

    # ---- CatNat
    cat = g["catnat"].copy()
    cat["code_insee"] = cat["code_commune"].map(to_current)
    cat = cat.dropna(subset=["code_insee"])
    cat = cat[cat["date_debut"].str[:4] >= "1982"]
    cat = cat.drop_duplicates(subset=["code_insee", "id_gaspar", "num_risque_jo", "date_debut"])
    nb_catnat = cat.groupby("code_insee").size()
    # CatNat « sécheresse » = dommages dus au retrait-gonflement des argiles
    extra["risque_retrait_argiles"] |= set(cat.loc[cat["num_risque_jo"] == "SEC", "code_insee"])
    # (pas de CatNat « chocs mécaniques des vagues » : les arrêtés tempête 1999/2009/2010 les attribuent
    #  à des départements entiers, y compris à l'intérieur des terres)
    extra["risque_avalanche"] |= set(cat.loc[cat["num_risque_jo"] == "AVA", "code_insee"])

    for col, s in extra.items():
        idx = df.index.isin(list(s))
        df.loc[idx, col] = 1
    # Communes absentes du DDRM (lacunes : Puy-de-Dôme, Drôme, Somme, Oise, Aisne…) : on garde
    # uniquement les 1 issus des PPR/CatNat, le reste reste inconnu (vide).

    # CatNat : base exhaustive depuis 1982 -> 0 pour les communes sans arrêté (hors COM du Pacifique)
    df["nb_catnat"] = nb_catnat.reindex(df.index)
    pacifique = df.index.str[:3].isin(["975", "977", "978", "984", "986", "987", "988", "989"])
    df.loc[~pacifique & df["nb_catnat"].isna(), "nb_catnat"] = 0

    # ---- Zonage sismique
    zs = zipfile.ZipFile(download(SISMIQUE_URL, "France_zonage_sismique.zip", max_age_days=365))
    dbf = read_dbf(zs.read("France_zonage_sismique.dbf"))
    dbf["zone"] = pd.to_numeric(dbf["Sismicite"].str[:1], errors="coerce")
    dbf["code_insee"] = dbf["insee"].map(to_current)
    sism = dbf.dropna(subset=["code_insee", "zone"]).groupby("code_insee")["zone"].max()
    df["zone_sismicite"] = sism.reindex(df.index)
    # Mayotte (absente de la couche 2018) : zone 3 « modérée » sur tout le département (décret 2010-1255)
    df.loc[df.index.str.startswith("976") & df["zone_sismicite"].isna(), "zone_sismicite"] = 3

    # ---- Potentiel radon
    rad = pd.read_csv(download(RADON_URL, "radon_irsn_2019.csv", max_age_days=365), sep=";", dtype=str,
                      encoding="latin-1")
    rad["code_insee"] = rad["insee_com"].map(to_current)
    rad["classe"] = pd.to_numeric(rad["classe_potentiel"], errors="coerce")
    radon = rad.dropna(subset=["code_insee", "classe"]).groupby("code_insee")["classe"].max()
    df["potentiel_radon"] = radon.reindex(df.index)

    # Communes rétablies après défusion (ex. Cantal 2024) absentes des couches anciennes :
    # valeur de la commune la plus proche (zonages spatialement continus).
    fill_nearest(df, ["zone_sismicite", "potentiel_radon"])

    cols = ["risque_inondation", "risque_submersion_marine", "risque_mouvement_terrain",
            "risque_retrait_argiles", "risque_feux_foret", "risque_avalanche", "risque_industriel",
            "risque_nucleaire", "risque_minier", "zone_sismicite", "potentiel_radon", "nb_catnat", "nb_risques"]
    rows = []
    for code, r in df[cols].iterrows():
        row = {"code_insee": code}
        for c in cols:
            v = r[c]
            row[c] = None if pd.isna(v) else int(v)
        rows.append(row)
    write_csv("risques", rows, cols)

    src_gaspar = f"Géorisques – base GASPAR (DGPR), export du {millesime}"
    ddrm_desc = (" Commune absente de la liste GASPAR des risques recensés (≈ 9 % des communes, notamment "
                 "Puy-de-Dôme, Drôme, Somme, Oise, Aisne) : valeur inconnue sauf si un PPR ou un arrêté CatNat l'atteste.")

    def boolean(id_, label, desc, src=src_gaspar):
        return {"id": id_, "label": label, "group": "Risques", "unit": "", "type": "boolean", "better": "lower",
                "description": desc + ddrm_desc, "source": src, "decimals": 0}

    write_indicators("risques", [
        boolean("risque_inondation", "Risque inondation",
                "1 si le risque inondation (débordement, crue torrentielle, ruissellement, remontée de nappe, "
                "submersion marine) est recensé pour la commune ou si elle est couverte par un PPR inondation "
                "approuvé ou prescrit."),
        boolean("risque_submersion_marine", "Risque submersion marine",
                "1 si l'inondation par submersion marine est recensée ou si la commune est couverte par un PPR "
                "littoral / submersion marine approuvé ou prescrit."),
        boolean("risque_mouvement_terrain", "Risque mouvement de terrain",
                "1 si un mouvement de terrain est recensé (effondrement de cavités, chutes de blocs, glissement, "
                "recul du trait de côte, avancée dunaire) ou si la commune est couverte par un PPR mouvement de "
                "terrain actif. Le retrait-gonflement des argiles est traité à part."),
        boolean("risque_retrait_argiles", "Retrait-gonflement des argiles",
                "1 si les tassements différentiels (retrait-gonflement des argiles) sont recensés, si la commune "
                "est couverte par un PPR argiles actif, ou si elle a obtenu au moins un arrêté CatNat "
                "« sécheresse » depuis 1982.",
                src_gaspar + " (risques recensés, PPRN, CatNat)"),
        boolean("risque_feux_foret", "Risque feux de forêt",
                "1 si le risque feu de forêt est recensé ou si la commune est couverte par un PPR incendie de "
                "forêt actif."),
        boolean("risque_avalanche", "Risque avalanche",
                "1 si le risque avalanche est recensé, si la commune est couverte par un PPR avalanche actif ou "
                "si elle a fait l'objet d'un arrêté CatNat avalanche."),
        boolean("risque_industriel", "Risque industriel",
                "1 si le risque industriel (établissements SEVESO notamment : effets thermiques, de surpression, "
                "toxiques) est recensé ou si la commune est couverte par un plan de prévention des risques "
                "technologiques (PPRT) actif."),
        boolean("risque_nucleaire", "Risque nucléaire",
                "1 si le risque nucléaire est recensé (commune située dans le périmètre d'un plan particulier "
                "d'intervention autour d'une installation nucléaire)."),
        boolean("risque_minier", "Risque minier",
                "1 si un risque minier est recensé (affaissement, effondrement, inondation de terrains miniers, "
                "gaz de mine, échauffement de terrils) ou si la commune est couverte par un PPR minier actif."),
        {"id": "zone_sismicite", "label": "Zone de sismicité", "group": "Risques", "unit": "", "type": "category",
         "better": "lower",
         "description": "Zonage sismique réglementaire de la France (décret n° 2010-1255, en vigueur depuis le "
                        "1er mai 2011) : de 1 (très faible) à 5 (fort, Antilles). Communes fusionnées depuis 2018 : "
                        "zone la plus élevée des anciennes communes.",
         "source": "Ministère de la Transition écologique – zonage sismique 2011 (couche communale data.gouv.fr, 2018)",
         "decimals": 0,
         "categories": {"1": "Très faible", "2": "Faible", "3": "Modérée", "4": "Moyenne", "5": "Forte"}},
        {"id": "potentiel_radon", "label": "Potentiel radon", "group": "Risques", "unit": "", "type": "category",
         "better": "lower",
         "description": "Potentiel radon de la commune (arrêté du 27 juin 2018) : 1 = faible, 2 = faible avec "
                        "facteurs géologiques particuliers facilitant le transfert du radon, 3 = significatif. "
                        "Communes fusionnées : catégorie la plus élevée des anciennes communes.",
         "source": "IRSN / ASN – « Connaître le potentiel radon de ma commune » (2019, zonage 2018)",
         "decimals": 0,
         "categories": {"1": "Faible", "2": "Faible avec facteurs aggravants", "3": "Significatif"}},
        {"id": "nb_catnat", "label": "Arrêtés CatNat", "group": "Risques", "unit": "arrêtés", "type": "number",
         "better": "lower",
         "description": "Nombre de reconnaissances de l'état de catastrophe naturelle depuis 1982 (une par arrêté, "
                        "type de péril et événement : inondation/coulée de boue, sécheresse, tempête, mouvement de "
                        "terrain…). Communes fusionnées : reconnaissances des anciennes communes cumulées, un même "
                        "arrêté pour un même péril et un même événement n'étant compté qu'une fois.",
         "source": src_gaspar + " (table CatNat)", "decimals": 0},
        {"id": "nb_risques", "label": "Nombre de risques recensés", "group": "Risques", "unit": "risques",
         "type": "number", "better": "lower",
         "description": "Nombre de familles de risques majeurs recensées pour la commune (inondation, mouvement de "
                        "terrain, séisme, avalanche, feu de forêt, phénomènes climatiques, radon, industriel, "
                        "nucléaire, rupture de barrage, transport de matières dangereuses, engins de guerre, "
                        "minier…)." + ddrm_desc,
         "source": src_gaspar + " (risques recensés)", "decimals": 0},
    ])

    # couverture
    n = len(df)
    for c in cols:
        nn = df[c].notna().sum()
        extra_txt = f", moy={df[c].mean():.2f}" if nn else ""
        print(f"  {c:28s} {nn / n:6.1%}{extra_txt}", file=sys.stderr)


if __name__ == "__main__":
    main()
