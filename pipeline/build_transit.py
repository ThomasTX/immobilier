"""Construit le réseau ferroviaire pour le calcul d'isochrones train dans le navigateur.

Sources (GTFS open data) :
  - SNCF Voyageurs : TGV INOUI, OUIGO, Intercités, TER (train + car TER)
  - SNCF Transilien / RER (Île-de-France)

Sorties :
  public/data/transit/stations.json   [[nom, lat, lon, flags, departs_jour], ...]
                                      flags: 1=train, 2=TGV/OUIGO, 4=Transilien/RER, 8=car seulement
  public/data/transit/connections.bin connexions d'un jour de semaine type, triées par heure de départ
                                      u32 n | u16 from[n] | u16 to[n] | u16 dep[n] | u16 arr[n] | u32 trip[n]
                                      (heures en minutes depuis minuit, peuvent dépasser 1440)
  public/data/transit/footpaths.json  [[a, b, minutes], ...] correspondances à pied / métro parisien
  public/data/transit/meta.json       date de service, sources, stats
  pipeline/out/transport.csv          indicateurs par commune (distance gare, TGV, fréquence…)

Usage : python3 pipeline/build_transit.py [--date AAAAMMJJ]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PUBLIC_DATA, download, load_communes, write_csv, write_indicators  # noqa: E402

FEEDS = {
    "sncf": "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip",
    "transilien": "https://eu.ftp.opendatasoft.com/sncf/gtfs/transilien-gtfs.zip",
}

F_TRAIN, F_TGV, F_IDF, F_BUS = 1, 2, 4, 8

# Grands aéroports (lat, lon) pour l'indicateur de distance
AIRPORTS = {
    "Paris-CDG": (49.0097, 2.5479), "Paris-Orly": (48.7262, 2.3652), "Nice": (43.6584, 7.2159),
    "Lyon": (45.7256, 5.0811), "Marseille": (43.4393, 5.2214), "Toulouse": (43.6291, 1.3638),
    "Bordeaux": (44.8283, -0.7156), "Nantes": (47.1569, -1.6077), "Bâle-Mulhouse": (47.5896, 7.5299),
    "Lille": (50.5633, 3.0869), "Montpellier": (43.5762, 3.963), "Beauvais": (49.4544, 2.1128),
    "Strasbourg": (48.5383, 7.6282), "Ajaccio": (41.9236, 8.8029), "Bastia": (42.5527, 9.4837),
    "Biarritz": (43.4683, -1.5233), "Brest": (48.4479, -4.4185), "Rennes": (48.0695, -1.7348),
    "Genève": (46.2381, 6.1089), "Clermont-Ferrand": (45.7867, 3.1692), "Pau": (43.38, -0.4186),
    "Perpignan": (42.7404, 2.8707), "Toulon-Hyères": (43.0973, 6.146), "Grenoble": (45.3629, 5.3294),
    "Figari": (41.5006, 9.0978), "La Réunion": (-20.8871, 55.5103), "Pointe-à-Pitre": (16.2653, -61.5318),
    "Fort-de-France": (14.591, -60.9956), "Cayenne": (4.8192, -52.3604),
}


def read_csv(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def hms(s: str) -> int:
    h, m, sec = s.split(":")
    return int(h) * 60 + int(m) + (1 if int(sec) >= 30 else 0)


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def feed_range(z: zipfile.ZipFile) -> tuple[str, str]:
    lo, hi = "99999999", "00000000"
    names = z.namelist()
    if "calendar.txt" in names:
        for r in read_csv(z, "calendar.txt"):
            lo, hi = min(lo, r["start_date"]), max(hi, r["end_date"])
    for r in read_csv(z, "calendar_dates.txt"):
        if r["exception_type"] == "1":
            lo, hi = min(lo, r["date"]), max(hi, r["date"])
    return lo, hi


def active_services(z: zipfile.ZipFile, day: str) -> set[str]:
    d = date(int(day[:4]), int(day[4:6]), int(day[6:]))
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][d.weekday()]
    active = set()
    if "calendar.txt" in z.namelist():
        for r in read_csv(z, "calendar.txt"):
            if r["start_date"] <= day <= r["end_date"] and r[wd] == "1":
                active.add(r["service_id"])
    for r in read_csv(z, "calendar_dates.txt"):
        if r["date"] == day:
            if r["exception_type"] == "1":
                active.add(r["service_id"])
            else:
                active.discard(r["service_id"])
    return active


def pick_date(zips: dict[str, zipfile.ZipFile]) -> str:
    ranges = [feed_range(z) for z in zips.values()]
    lo = max(r[0] for r in ranges)
    hi = min(r[1] for r in ranges)
    d = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])) + timedelta(days=7)
    while d.weekday() != 1:  # mardi
        d += timedelta(days=1)
    s = d.strftime("%Y%m%d")
    if s > hi:
        raise SystemExit(f"Pas de mardi commun aux flux ({lo}–{hi}) ; utilisez --date")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="jour de service AAAAMMJJ (défaut : un mardi commun aux flux)")
    args = ap.parse_args()

    zips = {k: zipfile.ZipFile(download(u, f"gtfs_{k}.zip", max_age_days=7)) for k, u in FEEDS.items()}
    day = args.date or pick_date(zips)
    print(f"Jour de service : {day}", file=sys.stderr)

    # ---- stations (stop areas) ----
    raw_st: dict[str, dict] = {}  # key feed:stop_id -> {name, lat, lon}
    parent_of: dict[str, str] = {}
    for feed, z in zips.items():
        for r in read_csv(z, "stops.txt"):
            key = f"{feed}:{r['stop_id']}"
            parent = r.get("parent_station") or ""
            if parent:
                parent_of[key] = f"{feed}:{parent}"
            raw_st[key] = {"name": r["stop_name"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}

    def station_key(feed: str, stop_id: str) -> str:
        k = f"{feed}:{stop_id}"
        return parent_of.get(k, k)

    # ---- trips actifs ----
    conns_raw = []  # (from_key, to_key, dep, arr, trip_idx)
    st_flags: dict[str, int] = defaultdict(int)
    st_deps: dict[str, int] = defaultdict(int)
    trip_counter = 0
    for feed, z in zips.items():
        services = active_services(z, day)
        route_type = {r["route_id"]: r["route_type"] for r in read_csv(z, "routes.txt")}
        trips = {}
        for r in read_csv(z, "trips.txt"):
            if r["service_id"] in services:
                trips[r["trip_id"]] = route_type.get(r["route_id"], "2")
        print(f"  {feed}: {len(services)} services actifs, {len(trips)} trajets", file=sys.stderr)
        by_trip: dict[str, list] = defaultdict(list)
        for r in read_csv(z, "stop_times.txt"):
            t = r["trip_id"]
            if t not in trips:
                continue
            by_trip[t].append((int(r["stop_sequence"]), r["stop_id"], r["arrival_time"], r["departure_time"],
                               r.get("pickup_type", "0"), r.get("drop_off_type", "0")))
        for t, sts in by_trip.items():
            sts.sort()
            rt = trips[t]
            tid = trip_counter
            trip_counter += 1
            prev = None
            for seq, sid, arr, dep, pick, drop in sts:
                sk = station_key(feed, sid)
                flag = F_BUS if rt == "3" else F_TRAIN
                if feed == "transilien":
                    flag |= F_IDF
                if feed == "sncf" and ("TGV" in sid or "OUIGO" in sid or "Lyria" in sid or "ICE" in sid):
                    flag |= F_TGV
                st_flags[sk] |= flag
                if pick != "1":
                    st_deps[sk] += 1
                cur = (sk, hms(arr or dep), hms(dep or arr), pick, drop)
                if prev is not None and prev[0] != sk:
                    conns_raw.append((prev[0], sk, prev[2], cur[1], tid))
                prev = cur
    print(f"  {len(conns_raw)} connexions, {trip_counter} trajets", file=sys.stderr)

    # ---- fusion spatiale des stations (< 120 m) entre flux ----
    used = sorted({c[0] for c in conns_raw} | {c[1] for c in conns_raw})
    lat = np.array([raw_st[k]["lat"] for k in used])
    lon = np.array([raw_st[k]["lon"] for k in used])
    parent = list(range(len(used)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = np.argsort(lat)
    slat = lat[order]
    for ii, i in enumerate(order):
        j = ii + 1
        while j < len(order) and slat[j] - lat[i] < 0.0012:
            k = order[j]
            if haversine(lat[i], lon[i], lat[k], lon[k]) < 0.12:
                a, b = find(i), find(int(k))
                if a != b:
                    parent[b] = a
            j += 1
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(used)):
        groups[find(i)].append(i)

    stations = []  # [name, lat, lon, flags, deps]
    key_to_idx: dict[str, int] = {}
    for root, members in groups.items():
        idx = len(stations)
        # nom : celui de la station SNCF nationale en priorité, sinon le plus court
        names = sorted((("transilien" in used[m]), len(raw_st[used[m]]["name"]), raw_st[used[m]]["name"]) for m in members)
        name = names[0][2]
        name = name.title() if name.isupper() else name
        fl = 0
        deps = 0
        for m in members:
            key_to_idx[used[m]] = idx
            fl |= st_flags[used[m]]
            deps += st_deps[used[m]]
        if fl & F_TRAIN:
            fl &= ~F_BUS
        stations.append([name, round(float(lat[members].mean()), 5), round(float(lon[members].mean()), 5), fl, deps])
    n_st = len(stations)
    print(f"  {n_st} stations après fusion", file=sys.stderr)
    if n_st >= 65535:
        raise SystemExit("trop de stations pour u16")

    # ---- connexions binaires ----
    arr_np = np.array([(key_to_idx[a], key_to_idx[b], d, r, t) for a, b, d, r, t in conns_raw if key_to_idx[a] != key_to_idx[b]],
                      dtype=np.int64)
    arr_np = arr_np[(arr_np[:, 2] >= 0) & (arr_np[:, 3] >= arr_np[:, 2]) & (arr_np[:, 2] < 65000)]
    arr_np = arr_np[np.lexsort((arr_np[:, 3], arr_np[:, 2]))]
    n = len(arr_np)
    out_dir = PUBLIC_DATA / "transit"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "connections.bin", "wb") as f:
        f.write(np.array([n], dtype="<u4").tobytes())
        for col in range(4):
            f.write(arr_np[:, col].astype("<u2").tobytes())
        if (n * 8) % 4:
            f.write(b"\0\0")
        f.write(arr_np[:, 4].astype("<u4").tobytes())
    print(f"  connections.bin : {n} connexions, {(out_dir / 'connections.bin').stat().st_size / 1e6:.1f} Mo", file=sys.stderr)

    # ---- correspondances : marche (< 700 m) + métro parisien (heuristique) ----
    slat = np.array([s[1] for s in stations])
    slon = np.array([s[2] for s in stations])
    foot = []
    paris_c = (48.8590, 2.3470)
    in_paris = haversine(slat, slon, paris_c[0], paris_c[1]) < 7.0
    for i in range(n_st):
        near = np.where((np.abs(slat - slat[i]) < 0.1) & (np.abs(slon - slon[i]) < 0.15))[0]
        d = haversine(slat[i], slon[i], slat[near], slon[near])
        for j, dj in zip(near, d):
            if j <= i:
                continue
            if dj < 0.7:
                m = 3 + dj * 1000 * 1.3 / 80  # 80 m/min, détour 1.3
            elif in_paris[i] and in_paris[j] and dj < 10:
                m = 12 + 3.0 * dj  # métro / RER intra-muros approx.
            else:
                continue
            foot.append([int(i), int(j), int(round(m))])
    (out_dir / "footpaths.json").write_text(json.dumps(foot, separators=(",", ":")))
    (out_dir / "stations.json").write_text(json.dumps(stations, ensure_ascii=False, separators=(",", ":")))
    meta = {
        "service_date": day,
        "n_stations": n_st,
        "n_connections": int(n),
        "n_footpaths": len(foot),
        "sources": ["SNCF Voyageurs GTFS (TGV, OUIGO, Intercités, TER)", "SNCF Transilien GTFS (RER, Transilien)"],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    # ---- indicateurs par commune ----
    communes = load_communes()
    clat = np.array([float(c["lat"]) for c in communes])
    clon = np.array([float(c["lon"]) for c in communes])
    flags = np.array([s[3] for s in stations])
    deps = np.array([s[4] for s in stations])
    train_idx = np.where(flags & F_TRAIN)[0]
    tgv_idx = np.where(flags & F_TGV)[0]
    rows = []
    near_lists = []
    for ci, c in enumerate(communes):
        d_tr = haversine(clat[ci], clon[ci], slat[train_idx], slon[train_idx])
        j = int(np.argmin(d_tr))
        d_tgv = haversine(clat[ci], clon[ci], slat[tgv_idx], slon[tgv_idx])
        jt = int(np.argmin(d_tgv))
        # fréquence : départs/jour de la gare la plus desservie à moins de 10 km
        close = train_idx[d_tr < 10]
        freq = int(deps[close].max()) if len(close) else 0
        ap_d = min(float(haversine(clat[ci], clon[ci], a[0], a[1])) for a in AIRPORTS.values())
        rows.append({
            "code_insee": c["code_insee"],
            "dist_gare_km": round(float(d_tr[j]), 1),
            "gare_proche": stations[train_idx[j]][0],
            "dist_gare_tgv_km": round(float(d_tgv[jt]), 1),
            "gare_tgv_proche": stations[tgv_idx[jt]][0],
            "freq_gare_10km": freq,
            "dist_aeroport_km": round(ap_d, 1),
        })
    write_csv("transport", rows, ["dist_gare_km", "gare_proche", "dist_gare_tgv_km", "gare_tgv_proche",
                                  "freq_gare_10km", "dist_aeroport_km"])
    write_indicators("transport", [
        {"id": "dist_gare_km", "label": "Distance à la gare la plus proche", "group": "Transports", "unit": "km",
         "type": "number", "better": "lower", "decimals": 1,
         "description": "Distance à vol d'oiseau du centre de la commune à la gare ferroviaire (TER, Transilien, RER, Intercités, TGV) la plus proche",
         "source": f"GTFS SNCF + Transilien ({day})"},
        {"id": "dist_gare_tgv_km", "label": "Distance à une gare TGV", "group": "Transports", "unit": "km",
         "type": "number", "better": "lower", "decimals": 1,
         "description": "Distance à vol d'oiseau à la gare desservie par TGV INOUI / OUIGO / Lyria / ICE la plus proche",
         "source": f"GTFS SNCF ({day})"},
        {"id": "freq_gare_10km", "label": "Desserte ferroviaire (départs/jour)", "group": "Transports", "unit": "départs/j",
         "type": "number", "better": "higher", "decimals": 0, "scale": "log",
         "description": "Nombre de départs de trains un mardi type depuis la gare la mieux desservie à moins de 10 km",
         "source": f"GTFS SNCF + Transilien ({day})"},
        {"id": "dist_aeroport_km", "label": "Distance à un aéroport", "group": "Transports", "unit": "km",
         "type": "number", "better": "lower", "decimals": 0,
         "description": "Distance à vol d'oiseau au grand aéroport le plus proche (liste de ~30 aéroports principaux)",
         "source": "Coordonnées des aéroports"},
    ])


if __name__ == "__main__":
    main()
