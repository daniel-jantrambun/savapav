import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { type Activity, api, type Category, type Stats } from "../api";
import { BarChart, DotChart } from "../charts";
import {
  activityLabel,
  CATEGORIES,
  CATEGORY_ORDER,
  dayLabel,
  duration,
  km,
  MULTISPORT_LABELS,
  minutes,
  speedLabel,
} from "../format";
import { Alert, Card, Checkbox, errorMessage, Spinner, useShowHidden } from "../ui";

const PERIODS = [
  { weeks: 12, label: "12 sem." },
  { weeks: 26, label: "6 mois" },
  { weeks: 52, label: "1 an" },
  { weeks: 104, label: "2 ans" },
  { weeks: 156, label: "3 ans" },
  { weeks: 260, label: "5 ans" },
  { weeks: 520, label: "10 ans" },
  // 0 : tout l'historique, depuis la première activité.
  { weeks: 0, label: "Tout" },
];
const PACE_STEPS = [5, 10, 15, 20, 30, 60, 120, 300];
const SAVED_KEY = "savapav.graphs";

type Saved = { type?: string; periode?: string };

// Derniers choix (type, période), retenus par le navigateur : repris quand l'URL ne les précise pas.
function readSaved(): Saved {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function save(saved: Saved) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(saved));
  } catch {
    // stockage indisponible (navigation privée) : le choix vaut pour cette visite seulement
  }
}

function chipClass(active: boolean): string {
  return `min-h-10 rounded-lg border px-3 text-sm ${
    active ? "border-accent bg-accent text-accent-ink" : "border-border bg-surface text-ink"
  }`;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

export function GraphsPage() {
  const [params, setParams] = useSearchParams();
  const [saved] = useState(readSaved);
  const periode = Number(params.get("periode") ?? saved.periode ?? NaN);
  const weeks = PERIODS.some((p) => p.weeks === periode) || periode >= 4 ? periode : 26;
  const [stats, setStats] = useState<Stats | null>(null);
  const [list, setList] = useState<Activity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useShowHidden();

  useEffect(() => {
    api
      .stats(weeks, showHidden)
      .then(setStats)
      .catch((e) => setError(errorMessage(e)));
  }, [weeks, showHidden]);

  const available = stats ? CATEGORY_ORDER.filter((c) => stats.categories[c]) : [];
  // Type mémorisé : seulement s'il a des activités sur la période (l'URL, elle, est suivie telle quelle).
  const fromSaved = saved.type && available.includes(saved.type as Category) ? saved.type : null;
  const requested = (params.get("type") ?? fromSaved) as Category | null;
  const cat: Category | undefined = requested && CATEGORIES[requested] ? requested : available[0];

  useEffect(() => {
    if (!cat) return;
    setList(null);
    api
      .activitiesCat(5000, cat, weeks, showHidden)
      .then(setList)
      .catch((e) => setError(errorMessage(e)));
  }, [cat, weeks, showHidden]);

  function select(next: { type?: Category; periode?: number }) {
    const p = new URLSearchParams(params);
    p.set("type", next.type ?? cat ?? "");
    p.set("periode", String(next.periode ?? weeks));
    if (!p.get("type")) p.delete("type");
    save({ type: p.get("type") ?? undefined, periode: p.get("periode") ?? undefined });
    setParams(p, { replace: true });
  }

  if (error) return <Alert>{error}</Alert>;
  if (!stats) return <Spinner />;

  const data = cat ? stats.categories[cat] : undefined;
  const hasDistance = !!data && data.total.km > 0 && cat !== "gym";
  const isSpeed = cat === "bike";
  // Durée réellement affichée : chaque discipline démarre à sa première activité.
  const multiYear = (data?.weeks.length ?? 0) > 52;
  // Au-delà de 5 ans, le serveur regroupe par mois.
  const monthly = data?.bucket === "month";
  const per = monthly ? "par mois" : "par semaine";
  // Infobulles : date complète (avec l'année au-delà d'un an), ou mois et année.
  const weekLabels =
    data?.weeks.map((w) =>
      monthly
        ? `Mois de ${dayLabel(w.start, { month: "long", year: "numeric" })}`
        : `Semaine du ${dayLabel(w.start, {
            day: "numeric",
            month: multiYear ? "numeric" : "short",
            year: multiYear ? "numeric" : undefined,
          })}`,
    ) ?? [];
  // Axe des abscisses : étiquettes courtes (~48 px chacune), « janv. 26 » au-delà d'un an.
  const axisLabels =
    data?.weeks.map((w) =>
      dayLabel(w.start, multiYear ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" }),
    ) ?? [];

  return (
    <>
      <div className="space-y-2">
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Type d'activité</legend>
          {(available.length ? available : CATEGORY_ORDER.slice(0, 1)).map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={c === cat}
              onClick={() => select({ type: c })}
              className={chipClass(c === cat)}
            >
              <span aria-hidden>{CATEGORIES[c].icon}</span> {CATEGORIES[c].label}
            </button>
          ))}
        </fieldset>
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Période</legend>
          {PERIODS.map((p) => (
            <button
              key={p.weeks}
              type="button"
              aria-pressed={p.weeks === weeks}
              onClick={() => select({ periode: p.weeks })}
              className={chipClass(p.weeks === weeks)}
            >
              {p.label}
            </button>
          ))}
        </fieldset>
        <Checkbox label="Afficher les activités cachées" checked={showHidden} onChange={setShowHidden} />
      </div>

      {!cat || !data ? (
        <Card>
          <p className="text-sm text-ink-2">
            Aucune activité sur cette période.{" "}
            <Link to="/activites" className="text-accent underline">
              Importez vos activités
            </Link>{" "}
            pour voir vos graphes.
          </p>
        </Card>
      ) : (
        <>
          <Card
            title={`${CATEGORIES[cat].label} · ${PERIODS.find((p) => p.weeks === weeks)?.label ?? `${weeks} sem.`}`}
          >
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Tile label="Séances" value={String(data.total.sessions)} />
              <Tile label="Durée" value={minutes(data.total.minutes)} />
              {hasDistance && <Tile label="Distance" value={km(data.total.km)} />}
              {data.total.speed_m_s && (
                <Tile
                  label={isSpeed ? "Vitesse moyenne" : "Allure moyenne"}
                  value={speedLabel(cat, data.total.speed_m_s)}
                />
              )}
              {data.total.avg_hr && <Tile label="FC moyenne" value={`${data.total.avg_hr} bpm`} />}
            </dl>
            <div className="mt-4 space-y-5">
              <BarChart
                label={hasDistance ? `Distance ${per} (km)` : `Durée ${per}`}
                values={data.weeks.map((w) => (hasDistance ? w.km : w.minutes))}
                labels={axisLabels}
                formatY={(v) => (hasDistance ? String(v) : minutes(v))}
                tooltip={(i) => {
                  const w = data.weeks[i];
                  return (
                    <>
                      <p className="text-ink-3 text-nowrap">{weekLabels[i]}</p>
                      <p className="font-medium tabular-nums">
                        {w.sessions} séance{w.sessions > 1 ? "s" : ""} · {minutes(w.minutes)}
                      </p>
                      {hasDistance && <p className="tabular-nums text-ink-2">{km(w.km)}</p>}
                    </>
                  );
                }}
              />
              {data.total.speed_m_s && (
                <DotChart
                  label={
                    isSpeed
                      ? `Vitesse moyenne ${per} (km/h)`
                      : `Allure moyenne ${per} (${cat === "swim" ? "/100 m" : "/km"}, plus haut = plus rapide)`
                  }
                  values={data.weeks.map((w) =>
                    w.speed_m_s
                      ? isSpeed
                        ? w.speed_m_s * 3.6
                        : (cat === "swim" ? 100 : 1000) / w.speed_m_s
                      : null,
                  )}
                  labels={axisLabels}
                  invert={!isSpeed}
                  ySteps={isSpeed ? undefined : PACE_STEPS}
                  formatY={(v) =>
                    isSpeed
                      ? v.toLocaleString("fr-FR", { maximumFractionDigits: 1 })
                      : `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}`
                  }
                  tooltip={(i) => {
                    const w = data.weeks[i];
                    return (
                      <>
                        <p className="text-ink-3">{weekLabels[i]}</p>
                        <p className="font-medium tabular-nums">
                          {w.speed_m_s ? speedLabel(cat, w.speed_m_s) : "—"}
                        </p>
                        <p className="tabular-nums text-ink-2">
                          {w.sessions} séance{w.sessions > 1 ? "s" : ""}
                        </p>
                      </>
                    );
                  }}
                />
              )}
              {data.total.avg_hr && (
                <DotChart
                  label={`FC moyenne ${per} (bpm)`}
                  values={data.weeks.map((w) => w.avg_hr)}
                  labels={axisLabels}
                  formatY={(v) => String(Math.round(v))}
                  tooltip={(i) => {
                    const w = data.weeks[i];
                    return (
                      <>
                        <p className="text-ink-3">{weekLabels[i]}</p>
                        <p className="font-medium tabular-nums">{w.avg_hr} bpm</p>
                        <p className="tabular-nums text-ink-2">
                          {w.sessions} séance{w.sessions > 1 ? "s" : ""}
                        </p>
                      </>
                    );
                  }}
                />
              )}
            </div>
          </Card>

          <Card title="Activités">{list === null ? <Spinner /> : <ActivityList activities={list} />}</Card>
        </>
      )}
    </>
  );
}

export function ActivityList({ activities }: { activities: Activity[] }) {
  if (activities.length === 0) return <p className="text-sm text-ink-2">Aucune activité.</p>;
  return (
    <ul className="divide-y divide-border">
      {activities.map((a) => (
        <li key={a.id}>
          <Link
            to={`/activites/${encodeURIComponent(a.id)}`}
            className="-mx-2 flex items-center justify-between gap-3 rounded-lg px-2 py-2 text-sm hover:bg-surface-2"
          >
            <div>
              <p>
                <span aria-hidden>{CATEGORIES[a.category].icon} </span>
                {activityLabel(a)}
                {a.multisport && (
                  <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-2">
                    {MULTISPORT_LABELS[a.multisport.kind]} {a.multisport.position}/{a.multisport.count}
                  </span>
                )}
                {a.hidden && (
                  <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-3">Cachée</span>
                )}
              </p>
              <p className="text-xs text-ink-3 first-letter:uppercase">
                {new Date(a.start_time).toLocaleDateString("fr-FR", {
                  weekday: "short",
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <div className="text-right tabular-nums">
                <p>
                  {a.distance_m ? `${km(a.distance_m / 1000)} · ` : ""}
                  {duration(a.duration_s)}
                </p>
                <p className="text-xs text-ink-3">
                  {a.distance_m && a.category !== "gym"
                    ? speedLabel(a.category, a.distance_m / a.duration_s)
                    : ""}
                  {a.avg_hr ? `${a.distance_m ? " · " : ""}${Math.round(a.avg_hr)} bpm` : ""}
                </p>
              </div>
              <span aria-hidden className="text-ink-3">
                ›
              </span>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
