export type Better = "higher" | "lower" | null;

export interface Indicator {
  id: string;
  label: string;
  group: string;
  unit?: string;
  type: "number" | "boolean" | "category";
  better: Better;
  description?: string;
  source?: string;
  decimals?: number;
  scale?: "linear" | "log";
  categories?: Record<string, string>;
  coverage?: number;
}

export interface CommunesData {
  generated: string;
  codes: string[];
  noms: string[];
  deps: string[];
  cps: string[];
  lat: number[];
  lon: number[];
  indicators: Indicator[];
  values: Record<string, (number | null)[]>;
  texts: Record<string, string[]>;
}

export interface TransitData {
  /** [nom, lat, lon, flags, départs/jour] */
  stations: [string, number, number, number, number][];
  n: number;
  from: Uint16Array;
  to: Uint16Array;
  dep: Uint16Array;
  arr: Uint16Array;
  trip: Uint32Array;
  /** adjacence des correspondances : pour chaque station, [voisine, minutes][] */
  foot: [number, number][][];
  meta: { service_date: string; n_stations: number; n_connections: number };
}

export const F_TRAIN = 1;
export const F_TGV = 2;
export const F_IDF = 4;
export const F_BUS = 8;

export type TravelMode = "car" | "train" | "best";
export type AccessMode = "walk" | "bike" | "car";

export interface Destination {
  id: string;
  name: string;
  label: string; // adresse affichée
  lat: number;
  lon: number;
  mode: TravelMode;
  maxMinutes: number;
  required: boolean; // filtre éliminatoire
  weight: number; // 0-3 dans le score
  /** train : heure d'arrivée souhaitée (minutes depuis minuit) */
  arriveBy: number;
  /** train : fenêtre de recherche avant l'heure d'arrivée (minutes) */
  window: number;
  access: AccessMode;
  maxAccess: number; // minutes max pour rejoindre la gare de départ
  color: string;
  enabled: boolean;
}

export interface NumFilter {
  min?: number | null;
  max?: number | null;
  /** pour booléens : 'yes' | 'no' ; pour catégories : valeurs autorisées */
  bool?: "yes" | "no" | null;
  cats?: string[] | null;
  weight: number; // 0 = n'entre pas dans le score
  prefer?: "higher" | "lower" | null; // sens du score si l'indicateur n'en a pas
  excludeMissing?: boolean;
}

export type ListingType = "appartement" | "maison" | "terrain" | "autre";
export type ListingStatus = "repere" | "a_visiter" | "visite" | "offre" | "ecarte";

export interface Listing {
  id: string;
  title: string;
  url: string;
  type: ListingType;
  price: number | null;
  surface: number | null; // habitable m²
  land: number | null; // terrain m²
  rooms: number | null;
  bedrooms: number | null;
  dpe: string; // A..G ou ''
  address: string;
  lat: number | null;
  lon: number | null;
  insee: string;
  status: ListingStatus;
  rating: number; // 0-5
  notes: string;
  features: string[]; // jardin, garage, piscine...
  createdAt: string;
  /** temps de trajet calculés (minutes) par destination */
  travel?: Record<string, { car?: number | null; train?: number | null }>;
}

export interface Settings {
  colorBy: string; // 'score' | id d'indicateur | 'time:<destId>'
  showFailing: boolean;
  departements: string; // liste "74, 73, 01" (vide = toute la France)
  basemap: "positron" | "ign" | "ortho";
  showStations: boolean;
}

export interface AppConfig {
  destinations: Destination[];
  filters: Record<string, NumFilter>;
  settings: Settings;
}
