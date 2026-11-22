"""Outils partagés par les scripts du pipeline de données.

Contrat pour chaque source (pipeline/sources/<nom>.py) :
  - télécharge ses données brutes via `download()` (cache dans pipeline/.cache/)
  - écrit pipeline/out/<nom>.csv : 1re colonne `code_insee` (code commune COG,
    arrondissements de Paris/Lyon/Marseille agrégés sur la commune parente),
    puis une colonne par indicateur (numérique, vide si inconnu)
  - écrit pipeline/indicators/<nom>.json : métadonnées des indicateurs
    (voir `write_indicators`).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
OUT = ROOT / "out"
IND = ROOT / "indicators"
PUBLIC_DATA = ROOT.parent / "public" / "data"
for d in (CACHE, OUT, IND, PUBLIC_DATA):
    d.mkdir(parents=True, exist_ok=True)

UA = "immobilier-pipeline/1.0 (+open data)"


def download(url: str, name: str | None = None, max_age_days: float = 30) -> Path:
    """Télécharge `url` dans le cache (réutilisé s'il a moins de max_age_days)."""
    if name is None:
        name = hashlib.sha1(url.encode()).hexdigest()[:12] + "_" + url.rstrip("/").split("/")[-1].split("?")[0][:60]
    path = CACHE / name
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
        return path
    print(f"  ↓ {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = path.with_suffix(path.suffix + ".part")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.replace(path)
            return path
        except Exception as e:  # noqa: BLE001
            print(f"    tentative {attempt + 1} échouée: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Téléchargement impossible: {url}")


# Arrondissements municipaux -> commune parente
ARRONDISSEMENTS: dict[str, str] = {}
for i in range(1, 21):
    ARRONDISSEMENTS[f"751{i:02d}"] = "75056"
for i in range(1, 17):
    ARRONDISSEMENTS[f"132{i:02d}"] = "13055"
for i in range(1, 10):
    ARRONDISSEMENTS[f"6938{i}"] = "69123"


def normalize_insee(code: str) -> str:
    """Normalise un code commune (zéro initial, arrondissements -> commune)."""
    code = str(code).strip()
    if code.isdigit() and len(code) == 4:
        code = "0" + code
    return ARRONDISSEMENTS.get(code, code)


def load_communes() -> list[dict]:
    """Liste de base des communes (produite par sources/communes.py)."""
    p = OUT / "communes.csv"
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict], columns: list[str]) -> Path:
    """rows: dicts avec 'code_insee' + colonnes. None/NaN -> vide."""
    p = OUT / f"{name}.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code_insee", *columns])
        for r in rows:
            vals = []
            for c in columns:
                v = r.get(c)
                if v is None or (isinstance(v, float) and v != v):
                    vals.append("")
                elif isinstance(v, float):
                    vals.append(f"{v:.6g}")
                else:
                    vals.append(v)
            w.writerow([r["code_insee"], *vals])
    print(f"  → {p} ({len(rows)} lignes)", file=sys.stderr)
    return p


def write_indicators(name: str, indicators: list[dict]) -> Path:
    """Métadonnées d'indicateurs. Champs par indicateur :
    id (= nom de colonne), label, group, unit, type ('number'|'boolean'|'category'),
    better ('higher'|'lower'|null), description, source, decimals (int),
    scale ('linear'|'log', optionnel), categories ({valeur: libellé}, si category).
    """
    p = IND / f"{name}.json"
    p.write_text(json.dumps(indicators, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
