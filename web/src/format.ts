import type { Activity, Axis, BlockSession, Category, SessionKind, Sport, Week } from "./api";

export const KIND_LABELS: Record<SessionKind, string> = {
  easy: "Footing",
  long: "Sortie longue",
  tempo: "Seuil",
  intervals: "Fractionné",
  strides: "Footing + lignes droites",
  race: "Course",
};

export const PHASE_LABELS: Record<Week["phase"], string> = {
  base: "Base",
  build: "Construction",
  taper: "Affûtage",
  race: "Semaine de course",
};

export const SPORTS: Record<Sport, { label: string; icon: string; axes: Axis[] }> = {
  run: { label: "Course", icon: "🏃", axes: ["maintien", "endurance", "vitesse"] },
  bike: { label: "Vélo", icon: "🚴", axes: ["maintien", "endurance", "vitesse"] },
  swim: { label: "Natation", icon: "🏊", axes: ["maintien", "endurance", "vitesse", "technique"] },
  gym: { label: "Salle", icon: "🏋️", axes: ["maintien", "force"] },
};
export const SPORT_ORDER: Sport[] = ["run", "bike", "swim", "gym"];

export const CATEGORIES: Record<Category, { label: string; icon: string }> = {
  ...SPORTS,
  other: { label: "Autre", icon: "🥾" },
};
export const CATEGORY_ORDER: Category[] = [...SPORT_ORDER, "other"];

// Libellés des sports FIT bruts (pour « Autre » et la liste des activités).
export const RAW_SPORT_LABELS: Record<string, string> = {
  running: "Course",
  cycling: "Vélo",
  swimming: "Natation",
  walking: "Marche",
  hiking: "Randonnée",
  training: "Renforcement",
  fitness_equipment: "Salle",
  cross_country_skiing: "Ski de fond",
  alpine_skiing: "Ski",
  rowing: "Aviron",
  tennis: "Tennis",
};

export function activityLabel(a: { sport: string; sub_sport: string | null; category: Category }): string {
  if (a.sub_sport === "yoga") return "Yoga";
  if (a.sub_sport === "indoor_cycling") return "Home trainer";
  if (a.sub_sport === "treadmill") return "Tapis";
  return a.category === "other" ? (RAW_SPORT_LABELS[a.sport] ?? a.sport) : CATEGORIES[a.category].label;
}

/** Vitesse (m/s) → allure ou vitesse selon le sport : min/km, km/h, ou /100 m en natation. */
export function speedLabel(category: Category, metersPerSecond: number): string {
  if (category === "bike")
    return `${(metersPerSecond * 3.6).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} km/h`;
  if (category === "swim") return swimPace(100 / metersPerSecond);
  return pace(1000 / metersPerSecond);
}

export const AXIS_LABELS: Record<Axis, string> = {
  maintien: "Maintenir",
  endurance: "Endurance",
  vitesse: "Vitesse",
  technique: "Technique",
  force: "Force",
};

export const BLOCK_KIND_LABELS: Record<BlockSession["kind"], string> = {
  easy: "Endurance facile",
  long: "Sortie longue",
  tempo: "Seuil",
  intervals: "Fractionné",
  strides: "Footing + lignes droites",
  technique: "Technique",
  strength: "Renforcement",
};

export const GYM_FOCUS_LABELS: Record<NonNullable<BlockSession["focus"]>, string> = {
  lower: "bas du corps",
  upper: "haut du corps",
  full_body: "corps entier",
  core: "gainage et mobilité",
};

export function minutes(value: number): string {
  const h = Math.floor(value / 60);
  const m = Math.round(value % 60);
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}` : `${m} min`;
}

export function swimPace(secondsPer100m: number): string {
  const s = Math.round(secondsPer100m);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}/100 m`;
}

export function pace(secondsPerKm: number): string {
  const s = Math.round(secondsPerKm);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}/km`;
}

export function duration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}` : `${m}:${String(rest).padStart(2, "0")}`;
}

/** Temps de course avec les secondes : 23:34, 1:48:24. */
export function raceTime(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export const RACE_LABELS: Record<number, string> = {
  5000: "5 km",
  10000: "10 km",
  21097.5: "Semi",
  42195: "Marathon",
};

export function km(value: number): string {
  return `${value.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} km`;
}

// Les dates du plan sont des jours calendaires ("2026-10-13") : on les lit en heure locale.
export function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function dayLabel(
  day: string,
  opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" },
) {
  return parseDay(day).toLocaleDateString("fr-FR", opts);
}

export function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** "1:45:00", "45:00" ou "45" (minutes) → secondes ; null si illisible. */
export function parseTime(value: string): number | null {
  const parts = value.trim().split(":");
  if (parts.some((p) => !/^\d+$/.test(p)) || parts.length > 3) return null;
  const nums = parts.map(Number);
  if (nums.length === 1) return nums[0] * 60;
  if (nums.length === 2) return nums[0] * 60 + nums[1];
  return nums[0] * 3600 + nums[1] * 60 + nums[2];
}

export function distanceLabel(distanceKm: number): string {
  if (Math.abs(distanceKm - 42.195) < 0.1) return "Marathon";
  if (Math.abs(distanceKm - 21.1) < 0.1) return "Semi-marathon";
  return km(distanceKm);
}

export const MULTISPORT_LABELS: Record<NonNullable<Activity["multisport"]>["kind"], string> = {
  triathlon: "Triathlon",
  duathlon: "Duathlon",
  aquathlon: "Aquathlon",
  multisport: "Multisport",
};
