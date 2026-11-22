"""Indicateurs socio-démographiques communaux (INSEE, base du dossier complet).

Source unique : « Base du dossier complet » de l'INSEE (https://www.insee.fr/fr/statistiques/5359146),
qui rassemble par commune les résultats du recensement (3 millésimes) et de Filosofi.
Les millésimes sont détectés automatiquement à partir des noms de colonnes
(P{aa}_POP, MED{aa}, …) pour que le script reste valable à la prochaine édition.

Paris/Lyon/Marseille : la base est communale (communes entières) ; si des
arrondissements apparaissent, les taux sont recalculés à partir des
numérateurs/dénominateurs agrégés (Filosofi : moyenne pondérée par la population,
la médiane n'étant pas additive).
"""
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

URL = "https://www.insee.fr/fr/statistiques/fichier/5359146/dossier_complet_csv.zip"
CACHE_NAME = "insee_dossier_complet_csv.zip"


def zip_ok(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return bool(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def download_checked(url: str, name: str, tries: int = 4) -> Path:
    """download() + contrôle d'intégrité (le proxy coupe parfois les gros transferts)."""
    for _ in range(tries):
        path = download(url, name)
        if zip_ok(path):
            return path
        print("  archive tronquée, nouveau téléchargement", file=sys.stderr)
        path.unlink()
    raise RuntimeError(f"Téléchargement incomplet: {url}")


def last_year(cols, pattern: str) -> str:
    """Plus grand suffixe d'année (2 chiffres) pour un motif type r'P(\\d\\d)_POP'."""
    ys = sorted({m.group(1) for c in cols if (m := re.fullmatch(pattern, c))})
    if not ys:
        raise RuntimeError(f"aucune colonne {pattern}")
    return ys[-1]


def main():
    path = download_checked(URL, CACHE_NAME, )
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        data_name = max(names, key=lambda n: z.getinfo(n).file_size)  # le fichier de données est le plus gros
        with z.open(data_name) as f:
            header = f.readline().decode("utf-8").strip().split(";")
        header = [h.strip('"') for h in header]

        rp = last_year(header, r"P(\d\d)_POP")
        rp_years = sorted({m.group(1) for c in header if (m := re.fullmatch(r"P(\d\d)_POP", c))})
        # recensement précédent le plus proche de 5 ans avant le dernier
        rp_old = min((y for y in rp_years if y != rp), key=lambda y: abs(int(rp) - int(y) - 5))
        fi = last_year(header, r"MED(\d\d)")
        need = {
            "pop": f"P{rp}_POP", "pop_old": f"P{rp_old}_POP", "pop65": f"P{rp}_POP65P",
            "act": f"P{rp}_ACT1564", "chom": f"P{rp}_CHOM1564",
            "log": f"P{rp}_LOG", "rsec": f"P{rp}_RSECOCC", "vac": f"P{rp}_LOGVAC",
            "rp": f"P{rp}_RP", "prop": f"P{rp}_RP_PROP",
            "med": f"MED{fi}", "tp60": f"TP60{fi}",
        }
        missing = [v for v in need.values() if v not in header]
        if missing:
            raise RuntimeError(f"colonnes absentes: {missing}")
        print(f"  {data_name}: RP 20{rp} (évolution depuis 20{rp_old}), Filosofi 20{fi}", file=sys.stderr)
        with z.open(data_name) as f:
            df = pd.read_csv(f, sep=";", dtype=str, usecols=["CODGEO", *need.values()])

    for k, c in need.items():
        df[k] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
    df["code_insee"] = df["CODGEO"].map(normalize_insee)

    # Agrégation (utile seulement si des arrondissements sont présents)
    df["med_x_pop"] = df["med"] * df["pop"]
    df["tp60_x_pop"] = df["tp60"] * df["pop"]
    df["pop_fi"] = df["pop"].where(df["med"].notna())
    df["pop_tp"] = df["pop"].where(df["tp60"].notna())
    add = ["pop", "pop_old", "pop65", "act", "chom", "log", "rsec", "vac", "rp", "prop",
           "med_x_pop", "tp60_x_pop", "pop_fi", "pop_tp"]
    multi = df["code_insee"].duplicated(keep=False)
    if multi.any():
        agg = df[multi].groupby("code_insee")[add].sum(min_count=1).reset_index()
        single = df[~multi]
        single = single.assign(med_x_pop=single["med"], pop_fi=1.0, tp60_x_pop=single["tp60"], pop_tp=1.0)
        g = pd.concat([single[["code_insee", *add]], agg], ignore_index=True)
    else:
        g = df.assign(med_x_pop=df["med"], pop_fi=1.0, tp60_x_pop=df["tp60"], pop_tp=1.0)[["code_insee", *add]]
    g = g.set_index("code_insee")

    def ratio(num, den, k=100.0):
        return (g[num] / g[den] * k).where(g[den] > 0)

    n_years = int(rp) - int(rp_old)
    out = pd.DataFrame(index=g.index)
    out["revenu_median"] = (g["med_x_pop"] / g["pop_fi"]).where(g["pop_fi"] > 0)
    out["taux_pauvrete"] = (g["tp60_x_pop"] / g["pop_tp"]).where(g["pop_tp"] > 0)
    out["taux_chomage"] = ratio("chom", "act")
    out["part_residences_secondaires"] = ratio("rsec", "log")
    out["part_logements_vacants"] = ratio("vac", "log")
    out["part_proprietaires"] = ratio("prop", "rp")
    out["part_plus_65"] = ratio("pop65", "pop")
    out["evolution_population"] = (((g["pop"] / g["pop_old"]) ** (1 / n_years) - 1) * 100).where(
        (g["pop_old"] > 0) & (g["pop"] > 0))

    cols = list(out.columns)
    rnd = {"revenu_median": 0}
    rows = []
    for c in load_communes():
        code = c["code_insee"]
        r = {"code_insee": code}
        if code in out.index:
            for col in cols:
                v = out.at[code, col]
                r[col] = None if pd.isna(v) else round(float(v), rnd.get(col, 2))
        rows.append(r)
    write_csv("socio", rows, cols)

    RP, RPO, FI = f"20{rp}", f"20{rp_old}", f"20{fi}"
    src_rp = f"INSEE, recensement de la population {RP} (exploitation principale), base du dossier complet"
    src_fi = f"INSEE, Filosofi {FI} (fichier localisé social et fiscal), base du dossier complet"
    secret_fi = (" Non diffusé (vide) pour les communes de moins de 50 ménages / 100 personnes "
                 "fiscales (secret statistique), ainsi que pour Mayotte, la Guadeloupe et la Guyane selon les millésimes.")
    write_indicators("socio", [
        {"id": "revenu_median", "label": "Revenu médian", "group": "Économie", "unit": "€/an/UC",
         "type": "number", "better": None, "decimals": 0,
         "description": f"Médiane du revenu disponible par unité de consommation en {FI} (niveau de vie "
                        f"annuel, après impôts et prestations sociales)." + secret_fi, "source": src_fi},
        {"id": "taux_pauvrete", "label": "Taux de pauvreté", "group": "Économie", "unit": "%",
         "type": "number", "better": "lower", "decimals": 1,
         "description": f"Part de la population vivant sous le seuil de pauvreté (60 % du niveau de vie "
                        f"médian national) en {FI}. Diffusé seulement pour les communes d'au moins "
                        f"1 000 ménages fiscaux environ (secret statistique), vide sinon.", "source": src_fi},
        {"id": "taux_chomage", "label": "Taux de chômage", "group": "Économie", "unit": "%",
         "type": "number", "better": "lower", "decimals": 1,
         "description": f"Chômeurs de 15-64 ans / actifs de 15-64 ans, au sens du recensement {RP} "
                        f"(déclaratif, différent du taux BIT).", "source": src_rp},
        {"id": "part_residences_secondaires", "label": "Résidences secondaires", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des résidences secondaires et logements occasionnels dans l'ensemble des "
                        f"logements (recensement {RP}).", "source": src_rp},
        {"id": "part_logements_vacants", "label": "Logements vacants", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des logements vacants dans l'ensemble des logements (recensement {RP}).",
         "source": src_rp},
        {"id": "part_proprietaires", "label": "Propriétaires occupants", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des résidences principales occupées par leur propriétaire (recensement {RP}).",
         "source": src_rp},
        {"id": "part_plus_65", "label": "Part des 65 ans et plus", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des personnes de 65 ans ou plus dans la population (recensement {RP}).",
         "source": src_rp},
        {"id": "evolution_population", "label": "Évolution de la population", "group": "Démographie",
         "unit": "%/an", "type": "number", "better": None, "decimals": 2,
         "description": f"Taux de variation annuel moyen de la population municipale entre {RPO} et {RP} "
                        f"(géographie communale en vigueur).",
         "source": f"INSEE, recensements de la population {RPO} et {RP}, base du dossier complet"},
    ])

    n = len(rows)
    for col in cols:
        k = sum(1 for r in rows if r.get(col) is not None)
        print(f"  {col}: {k}/{n} ({100 * k / n:.1f} %)", file=sys.stderr)


if __name__ == "__main__":
    main()
