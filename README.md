# 🏡 Où acheter ? Recherche immobilière multicritère, France entière

Une application web qui croise **tous vos critères** pour trouver les communes, puis les biens, qui vous conviennent :

- **Isochrones voiture et train.**
  - Voiture : service d'isochrones de l'IGN.
  - Train : vrais horaires TGV, OUIGO, Intercités, TER, cars TER, RER et Transilien d'un mardi type, calculés dans le navigateur.
  - Vous indiquez une heure d'arrivée, une souplesse, et comment vous rejoignez la gare (à pied, à vélo, en voiture).
  - Plusieurs destinations sont possibles (travail, école, famille…), chacune éliminatoire ou simplement pondérée.
- **Plus de 60 critères par commune**, tous filtrables (min/max, oui/non, catégories) et pondérables dans un score :
  - prix DVF au m² (maisons, appartements) et leur évolution ;
  - loyers, rendement locatif, taxe foncière ;
  - location courte durée (Airbnb, via Inside Airbnb ou AirDNA) ;
  - gares, desserte, aéroports ;
  - écoles, médecins, urgences, commerces, fibre, accès aux soins (APL) ;
  - risques (inondation, argiles, séismes, radon, catastrophes naturelles…) ;
  - altitude, littoral, délinquance, revenus, démographie…
- **Carte** : les communes sont colorées par score, par trajet ou par n'importe quel critère. Les communes exclues sont grisées.
  - Au survol : infobulle. Au clic : fiche complète, avec des liens vers les annonces (Leboncoin, Bien'ici…), les ventes DVF et Géorisques.
  - Fonds disponibles : plan clair, plan IGN, photos aériennes.
- **Diagnostic** : quand il y a trop peu de résultats, l'app indique le critère qui élimine le plus de communes.
- **Mes biens** : suivi des annonces repérées.
  - Collez le texte d'une annonce : prix, surface, terrain, pièces et DPE sont détectés.
  - Chaque bien est comparé au prix médian de sa commune, avec ses temps de trajet voiture (itinéraire IGN) et train.
  - Statut, note, notes de visite, filtres, export CSV/JSON.
- Recherche sauvegardée automatiquement dans le navigateur, avec export/import du fichier de recherche.

## Lancer l'application

```bash
npm install
npm run dev
```

Puis ouvrez http://localhost:5173. Les données sont déjà incluses dans `public/data/`.

Pour une version statique, `npm run build` produit `dist/`, à servir par n'importe quel hébergement statique (GitHub Pages, Netlify…).

Les isochrones voiture, le géocodage d'adresses et les itinéraires utilisent les services gratuits de la Géoplateforme IGN (sans clé). Une connexion internet est donc nécessaire.

## Mettre à jour les données

```bash
pip install pandas numpy requests openpyxl
npm install          # pour mapshaper (simplification des contours)
npm run data         # = bash pipeline/run_all.sh
```

Le pipeline télécharge les sources open data (cache dans `pipeline/.cache/`). Il produit ensuite `public/data/communes.json`, `communes.topojson` et `transit/` (horaires). Mettez à jour les horaires régulièrement : le flux SNCF couvre quelques mois.

| Script | Contenu | Sources |
|---|---|---|
| `sources/communes.py` | référentiel, population, densité | geo.api.gouv.fr |
| `sources/immobilier.py` | prix DVF, évolution, loyers, rendement, taxe foncière | Etalab DVF, ANIL, DGFiP |
| `sources/location_courte.py` | location courte durée (prix/nuit, occupation, revenus) | AirDNA (clé ou exports), Inside Airbnb |
| `sources/equipements.py` | écoles, santé, commerces, services et distances | INSEE BPE |
| `sources/connectivite.py` | fibre, APL médecins | ARCEP, DREES |
| `sources/risques.py` | risques naturels et technologiques, CatNat, sismicité, radon | Géorisques |
| `sources/geographie.py` | altitude, littoral, distance à la mer… | IGN, ONML |
| `sources/socio.py`, `securite.py` | revenus, chômage, résidences secondaires, délinquance | INSEE, SSMSI |
| `build_transit.py` | réseau ferré et indicateurs de desserte | GTFS SNCF + Transilien |
| `build_geometry.py` | contours simplifiés | Etalab admin-express |
| `build_communes.py` | fusion en un fichier pour l'app | — |

Pour ajouter un critère, il suffit d'un script `pipeline/sources/<nom>.py` qui écrit `pipeline/out/<nom>.csv` (colonne `code_insee` + indicateurs) et `pipeline/indicators/<nom>.json` (métadonnées, voir `pipeline/common.py`). L'interface génère automatiquement filtres, pondération, coloration et fiche.

### AirDNA

AirDNA ne publie pas de données ouvertes. Deux façons de l'utiliser :

1. **API Enterprise** (abonnement payant) : `AIRDNA_API_KEY=... python3 pipeline/sources/location_courte.py`. La clé reste dans votre variable d'environnement ; elle n'est jamais écrite dans le dépôt.
2. **Exports** : déposez des CSV dans `pipeline/manual/airdna/` (format décrit dans son README).

Sans AirDNA, l'app utilise les données gratuites [Inside Airbnb](https://insideairbnb.com) (CC BY 4.0), disponibles pour quelques grandes zones françaises.

## Limites à connaître

- **Temps voiture** : vitesses moyennes sans trafic, mesurés au centre de la commune. C'est la borne haute du palier de 5 à 15 min qui le contient.
- **Temps train** :
  - un seul jour type (mardi) ; grèves et travaux ne sont pas pris en compte ;
  - le rabattement vers la gare est estimé à vol d'oiseau × 1,3 ;
  - le métro parisien est estimé ;
  - les transports urbains hors Île-de-France ne sont pas modélisés.
- **Précision des indicateurs** : ce sont des moyennes communales. Une grande commune peut être très hétérogène, et une commune « à risque inondation » n'est pas inondable partout. Vérifiez toujours à l'adresse (Géorisques, PLU, visite).
- **Données manquantes** : les prix DVF sont absents en Alsace-Moselle et à Mayotte (pas de DVF), et vides sous 5 ventes. Les critères à faible couverture l'indiquent dans l'interface.
