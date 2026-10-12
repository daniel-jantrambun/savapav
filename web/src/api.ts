// Client de l'API FastAPI (même origine : le cookie de session suit automatiquement).

export type User = {
  id: number;
  username: string;
  display_name: string;
  hr_rest: number;
  hr_max: number;
  is_admin: boolean;
};

export type NewUser = { username: string; password: string; display_name?: string; is_admin?: boolean };

export type Account = {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  created_at: string;
  garmin_connected: boolean;
  last_sync_at: string | null;
  garmin_error: string | null;
  activities: number;
  plans: number;
};

export type SessionKind = "easy" | "long" | "tempo" | "intervals" | "strides" | "race";

export type Session = {
  date: string;
  kind: SessionKind;
  distance_km: number;
  pace_s_per_km: number;
  reps: number | null;
  rep_m: number | null;
  recovery_s: number | null;
};

export type Week = {
  index: number;
  start: string;
  phase: "base" | "build" | "taper" | "race";
  volume_km: number;
  deload: boolean;
  sessions: Session[];
  text: string | null;
};

export type RaceGoal = {
  distance_km: number;
  target_time_s: number;
  race_date: string;
  runs_per_week: number;
};

// --- Multisport : pas d'objectif chiffré, un axe d'amélioration par sport ---------------------------------

export type Sport = "run" | "bike" | "swim" | "gym";
export type Axis = "maintien" | "endurance" | "vitesse" | "technique" | "force";
export type Focus = Partial<Record<Sport, Axis>>;

export type Preferences = { focus: Focus; sessions_per_week: number };

export type BlockSession = {
  date: string;
  sport: Sport;
  kind: "easy" | "long" | "tempo" | "intervals" | "strides" | "technique" | "strength";
  duration_min: number;
  hard: boolean;
  distance_km: number | null;
  pace_s_per_km: number | null;
  pace_s_per_100m: number | null;
  hr_low: number | null;
  hr_high: number | null;
  reps: number | null;
  rep_m: number | null;
  rep_s: number | null;
  recovery_s: number | null;
  focus: "lower" | "upper" | "full_body" | "core" | null;
};

export type BlockWeek = {
  index: number;
  start: string;
  deload: boolean;
  minutes: number;
  minutes_by_sport: Partial<Record<Sport, number>>;
  sessions: BlockSession[];
  text: string | null;
};

export type References = {
  run_paces: { easy: number; long: number; tempo: number; intervals: number };
  run_paces_source: "fc" | "allure_moyenne" | "defaut";
  swim_pace_s_per_100m: number;
  swim_pace_source: "allure_moyenne" | "defaut";
  hr_zones: Record<"endurance" | "tempo" | "seuil", [number, number]>;
};

export type SportProfile = {
  sessions: number;
  sessions_per_week: number;
  recent_sessions_per_week: number;
  minutes_per_week: number;
  km_per_week: number | null;
  baseline_minutes: number;
  longest_minutes: number;
  trend: "hausse" | "baisse" | "stable" | "nouveau";
  last_date: string;
};

export type Analysis = {
  weeks: number;
  sports: Partial<Record<Sport, SportProfile>>;
  others: { sport: string; sessions: number; minutes: number }[];
  references: References;
};

type PlanCommon = { id: number; created_at: string; narrating: boolean; narrate_error: string | null };

export type RacePlan = PlanCommon & {
  mode: "race";
  goal: RaceGoal & { mode: "race" };
  current_weekly_km: number;
  weeks: Week[];
  paces: Record<string, number>;
  warnings: string[];
};

export type BlockPlan = PlanCommon & {
  mode: "multisport";
  goal: Preferences & { mode: "multisport"; block: number; start: string };
  number: number;
  sessions_per_sport: Partial<Record<Sport, number>>;
  baseline_minutes: Partial<Record<Sport, number>>;
  references: References;
  weeks: BlockWeek[];
  warnings: string[];
};

export type Plan = RacePlan | BlockPlan;

export type PlanSummary = { id: number; created_at: string; goal: Plan["goal"] };

export type Category = Sport | "other";

export type Activity = {
  id: string;
  sport: string;
  sub_sport: string | null;
  category: Category;
  start_time: string;
  duration_s: number;
  distance_m: number | null;
  elevation_gain_m: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  avg_power: number | null;
  hidden: boolean;
  // Discipline d'une séance multisport (triathlon...) : position dans l'enchaînement, toutes les disciplines.
  multisport: {
    kind: "triathlon" | "duathlon" | "aquathlon" | "multisport";
    position: number;
    count: number;
    parts: { id: string; category: Category }[];
  } | null;
};

export type Streams = {
  t: number[];
  distance?: (number | null)[];
  speed?: (number | null)[];
  hr?: (number | null)[];
  altitude?: (number | null)[];
  power?: (number | null)[];
  cadence?: (number | null)[];
};

export type ActivityDetail = Activity & {
  streams: Streams | null;
  unavailable?: string;
  splits?: { distance_m: number; duration_s: number; avg_hr: number | null }[];
  hr_zones?: { zone: number; low: number | null; high: number | null; seconds: number }[];
  lengths?: number[];
  pool_length_m?: number;
};

export type WeekStat = {
  start: string;
  sessions: number;
  minutes: number;
  km: number;
  speed_m_s: number | null;
  avg_hr: number | null;
};

export type Stats = {
  weeks: number;
  // Au-delà de 5 ans, `weeks` contient des mois (`bucket: "month"`, `start` = 1er du mois).
  categories: Partial<
    Record<Category, { bucket: "week" | "month"; weeks: WeekStat[]; total: Omit<WeekStat, "start"> }>
  >;
};

export type LoadPoint = { day: string; load: number; ctl: number; atl: number; tsb: number };

export type SyncProgress = {
  phase: "connexion" | "liste" | "telechargement" | "import" | "termine" | "erreur" | null;
  running: boolean;
  found?: number;
  to_download?: number;
  downloaded?: number;
  imported?: number;
  error?: string | null;
  started_at?: string;
  finished_at?: string | null;
};

export type ImportProgress = {
  running: boolean;
  files?: number;
  imported?: number;
  skipped?: number;
  error?: string | null;
  started_at?: string;
  finished_at?: string | null;
};

export type GarminStatus = {
  connected: boolean;
  connected_at?: string;
  last_sync_at?: string | null;
  last_error?: string | null;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// Appelé sur une réponse 401 : l'application renvoie vers l'écran de connexion.
let onUnauthorized = () => {};
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    if (response.status === 401 && path !== "/login") onUnauthorized();
    let message = `Erreur ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = "Valeurs invalides, vérifiez le formulaire";
    } catch {
      // réponse sans JSON : on garde le message générique
    }
    throw new ApiError(response.status, message);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const api = {
  setupStatus: () => request<{ needs_setup: boolean }>("GET", "/setup"),
  setup: (account: NewUser) => request<User>("POST", "/setup", account),
  login: (username: string, password: string) => request<User>("POST", "/login", { username, password }),
  logout: () => request<void>("POST", "/logout"),
  me: () => request<User>("GET", "/me"),
  updateMe: (fields: Partial<Pick<User, "display_name" | "hr_rest" | "hr_max">>) =>
    request<User>("PATCH", "/me", fields),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("POST", "/me/password", { current_password, new_password }),

  garminStatus: () => request<GarminStatus>("GET", "/me/garmin"),
  garminConnect: (email: string, password: string) =>
    request<{ status: "connected" | "needs_mfa" }>("POST", "/me/garmin", { email, password }),
  garminMfa: (code: string) => request<{ status: "connected" }>("POST", "/me/garmin/mfa", { code }),
  garminDisconnect: () => request<void>("DELETE", "/me/garmin"),
  garminSync: () => request<{ imported: number; skipped: number }>("POST", "/me/garmin/sync"),
  garminSyncAll: () => request<SyncProgress>("POST", "/me/garmin/sync-all"),
  garminSyncAllStatus: () => request<SyncProgress>("GET", "/me/garmin/sync-all"),

  accounts: () => request<Account[]>("GET", "/admin/users"),
  createAccount: (account: NewUser) => request<User>("POST", "/admin/users", account),
  deleteAccount: (id: number) => request<void>("DELETE", `/admin/users/${id}`),
  resetPassword: (id: number, new_password: string) =>
    request<void>("POST", `/admin/users/${id}/password`, { new_password }),

  // hidden : inclure les activités cachées.
  activities: (limit = 50, category?: Category, offset = 0, hidden = false) =>
    request<Activity[]>(
      "GET",
      `/activities?limit=${limit}&offset=${offset}&hidden=${hidden}${category ? `&category=${category}` : ""}`,
    ),
  activitiesCat: (limit = 50, category: Category, weeks: number, hidden = false) =>
    request<Activity[]>(
      "GET",
      `/activities/category?limit=${limit}&weeks=${weeks}&category=${category}&hidden=${hidden}`,
    ),
  setActivityHidden: (id: string, hidden: boolean) =>
    request<{ hidden: boolean }>("PATCH", `/activities/${encodeURIComponent(id)}`, { hidden }),
  activity: (id: string) => request<ActivityDetail>("GET", `/activities/${encodeURIComponent(id)}`),
  stats: (weeks: number, hidden = false) => request<Stats>("GET", `/stats?weeks=${weeks}&hidden=${hidden}`),
  importFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportProgress>("POST", "/activities/import", form);
  },
  deleteActivities: () => request<{ deleted: number }>("DELETE", "/activities"),
  importStatus: () => request<ImportProgress>("GET", "/activities/import"),
  load: (days = 120) => request<LoadPoint[]>("GET", `/load?days=${days}`),

  plans: () => request<PlanSummary[]>("GET", "/plans"),
  plan: (id: number) => request<Plan>("GET", `/plans/${id}`),
  createPlan: (goal: RaceGoal) => request<RacePlan>("POST", "/plans", goal),
  analysis: () => request<Analysis>("GET", "/analysis"),
  createBlock: (prefs: Preferences) => request<BlockPlan>("POST", "/plans/multisport", prefs),
  deletePlan: (id: number) => request<void>("DELETE", `/plans/${id}`),
  narrate: (id: number) => request<{ status: string }>("POST", `/plans/${id}/narrate`),
};
