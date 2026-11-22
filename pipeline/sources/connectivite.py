"""Connectivité et accès aux soins de proximité par commune.

- ARCEP « Ma connexion internet » (statistiques communales, dernier trimestre publié) :
  part des locaux éligibles à la fibre FttH et au très haut débit filaire (>= 30 Mbit/s).
- DREES : Accessibilité Potentielle Localisée (APL) aux médecins généralistes,
  dernier millésime publié (onglet le plus récent du classeur).

Couverture mobile 4G/5G : non intégrée (l'ARCEP ne publie que des polygones de
couverture par opérateur, en GeoPackage de plusieurs centaines de Mo, inexploitables
sans bibliothèque SIG).
"""
from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

MCI_BASE = "https://data.arcep.fr/fixe/maconnexioninternet/statistiques/last/commune/"
URL_TECHNO = MCI_BASE + "commune_techno.csv"
URL_DEBIT_FILAIRE = MCI_BASE + "commune_debit_filaire.csv"
URL_APL_MG = ("https://data.drees.solidarites-sante.gouv.fr/api/v2/catalog/datasets/"
              "530_l-accessibilite-potentielle-localisee-apl/attachments/"
              "indicateur_d_apl_aux_medecins_generalistes_xlsx")

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# --------------------------------------------------------------------------- xlsx
def _xlsx_sheets(path: Path) -> list[tuple[str, str]]:
    """[(nom de l'onglet, chemin XML dans l'archive)] dans l'ordre du classeur."""
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for s in wb.iter(NS + "sheet"):
        t = targets[s.get(RNS + "id")].lstrip("/")
        out.append((s.get("name"), t if t.startswith("xl/") else "xl/" + t))
    return out


def _xlsx_rows(path: Path, member: str) -> list[dict[str, str | None]]:
    """Lecture minimale d'un onglet xlsx (stdlib) -> liste de {lettre colonne: valeur}."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
    rows = []
    for _, el in ET.iterparse(z.open(member)):
        if el.tag != NS + "row":
            continue
        row = {}
        for c in el.iter(NS + "c"):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            t, v = c.get("t"), c.find(NS + "v")
            if t == "inlineStr":
                row[col] = "".join(x.text or "" for x in c.iter(NS + "t"))
            elif v is None:
                row[col] = None
            elif t == "s":
                row[col] = shared[int(v.text)]
            else:
                row[col] = v.text
        rows.append(row)
        el.clear()
    return rows


# --------------------------------------------------------------------------- ARCEP
def load_arcep() -> tuple[pd.DataFrame, str]:
    techno = pd.read_csv(download(URL_TECHNO, "arcep_mci_commune_techno.csv"), sep=";",
                         dtype={"code_insee": str})
    filaire = pd.read_csv(download(URL_DEBIT_FILAIRE, "arcep_mci_commune_debit_filaire.csv"), sep=";",
                          dtype={"code_insee": str})
    techno = techno[techno["type"] == "all"]
    filaire = filaire[filaire["type"] == "all"]
    date = str(techno["date"].max())
    df = techno[["code_insee", "nbr", "elig_ftth"]].merge(
        filaire[["code_insee", "nbr", "elig_thd30"]].rename(columns={"nbr": "nbr_f"}),
        on="code_insee", how="outer")
    df["code_insee"] = df["code_insee"].map(normalize_insee)
    df = df.groupby("code_insee", as_index=False).sum(min_count=1)
    df["taux_ftth"] = 100 * df["elig_ftth"] / df["nbr"].where(df["nbr"] > 0)
    df["taux_thd30_filaire"] = 100 * df["elig_thd30"] / df["nbr_f"].where(df["nbr_f"] > 0)
    return df[["code_insee", "taux_ftth", "taux_thd30_filaire"]], date


def trimestre(date: str) -> str:
    y, m = int(date[:4]), int(date[5:7])
    return f"{y}T{(m - 1) // 3 + 1} (situation au {date[8:10]}/{date[5:7]}/{y})"


# --------------------------------------------------------------------------- DREES
def load_apl() -> tuple[pd.DataFrame, int]:
    path = download(URL_APL_MG, "drees_apl_medecins_generalistes.xlsx")
    best = None
    for name, member in _xlsx_sheets(path):
        m = re.search(r"(20\d\d)", name)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), member)
    if best is None:  # repli : dernier onglet
        best = (0, _xlsx_sheets(path)[-1][1])
    rows = _xlsx_rows(path, best[1])
    year = best[0]
    for r in rows[:5]:
        m = re.search(r"Millésime\s*:\s*(20\d\d)", r.get("A") or "")
        if m:
            year = int(m.group(1))
    hdr = next(i for i, r in enumerate(rows) if (r.get("A") or "").startswith("Code commune"))
    header = rows[hdr]
    col = {k: v.strip() for k, v in header.items() if v}
    c_apl = next(k for k, v in col.items() if v == "APL aux médecins généralistes")
    c_65 = next(k for k, v in col.items() if "65 ans" in v)
    c_pop = next(k for k, v in col.items() if v.startswith("Population standardisée"))
    recs = []
    for r in rows[hdr + 1:]:
        code = (r.get("A") or "").strip()
        if not re.fullmatch(r"[0-9AB]{5}|\d{4}", code):
            continue
        recs.append({"code_insee": normalize_insee(code),
                     "apl": pd.to_numeric(r.get(c_apl), errors="coerce"),
                     "apl65": pd.to_numeric(r.get(c_65), errors="coerce"),
                     "w": pd.to_numeric(r.get(c_pop), errors="coerce")})
    df = pd.DataFrame(recs)
    # Arrondissements éventuels : moyenne pondérée par la population standardisée (recommandation DREES)
    df["w"] = df["w"].fillna(0)
    out = []
    for code, g in df.groupby("code_insee"):
        if len(g) == 1:
            out.append({"code_insee": code, "apl_medecins": g["apl"].iloc[0], "apl_medecins_65": g["apl65"].iloc[0]})
        else:
            w = g["w"] if g["w"].sum() > 0 else pd.Series(1.0, index=g.index)
            res = {"code_insee": code}
            for src, dst in (("apl", "apl_medecins"), ("apl65", "apl_medecins_65")):
                ok = g[src].notna()
                res[dst] = (g.loc[ok, src] * w[ok]).sum() / w[ok].sum() if ok.any() and w[ok].sum() > 0 else None
            out.append(res)
    return pd.DataFrame(out), year


def main():
    communes = load_communes()
    arcep, date = load_arcep()
    apl, apl_year = load_apl()
    tri = trimestre(date)
    print(f"  ARCEP {tri} ; APL millésime {apl_year}", file=sys.stderr)

    base = pd.DataFrame({"code_insee": [c["code_insee"] for c in communes]})
    df = base.merge(arcep, on="code_insee", how="left").merge(apl, on="code_insee", how="left")
    cols = ["taux_ftth", "taux_thd30_filaire", "apl_medecins", "apl_medecins_65"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float).round(3 if c.startswith("apl") else 2)
    rows = df.to_dict("records")
    write_csv("connectivite", rows, cols)

    src_arcep = f"ARCEP, Ma connexion internet – statistiques communales, {tri}"
    src_apl = f"DREES, indicateur d'accessibilité potentielle localisée (APL) aux médecins généralistes, millésime {apl_year}"
    write_indicators("connectivite", [
        {"id": "taux_ftth", "label": "Locaux raccordables à la fibre", "group": "Services", "unit": "%",
         "type": "number", "better": "higher",
         "description": "Part des locaux (logements et locaux professionnels) de la commune éligibles à la fibre "
                        "optique jusqu'à l'abonné (FttH) auprès d'au moins un opérateur.",
         "source": src_arcep, "decimals": 0},
        {"id": "taux_thd30_filaire", "label": "Très haut débit filaire (≥ 30 Mbit/s)", "group": "Services",
         "unit": "%", "type": "number", "better": "higher",
         "description": "Part des locaux éligibles à un débit descendant d'au moins 30 Mbit/s par un réseau filaire "
                        "(fibre FttH, câble ou cuivre VDSL2), hors technologies radio et satellite.",
         "source": src_arcep, "decimals": 0},
        {"id": "apl_medecins", "label": "Accès aux médecins généralistes (APL)", "group": "Services",
         "unit": "consult./an/hab.", "type": "number", "better": "higher",
         "description": "Accessibilité potentielle localisée aux médecins généralistes : nombre de consultations "
                        "ou visites accessibles par an et par habitant standardisé (par l'âge), en tenant compte de "
                        "l'offre des communes voisines (jusqu'à 20 min) et de la demande. Un APL inférieur à 2,5 "
                        "correspond généralement à une zone sous-dotée (désert médical).",
         "source": src_apl, "decimals": 1},
        {"id": "apl_medecins_65", "label": "Accès aux généralistes de 65 ans ou moins (APL)", "group": "Services",
         "unit": "consult./an/hab.", "type": "number", "better": "higher",
         "description": "APL aux médecins généralistes en ne comptant que les médecins de 65 ans ou moins : "
                        "approche l'offre durable, hors médecins proches de la retraite.",
         "source": src_apl, "decimals": 1},
    ])

    n = len(df)
    for c in cols:
        print(f"  {c}: {df[c].notna().sum() / n:.1%} des communes", file=sys.stderr)


if __name__ == "__main__":
    main()
