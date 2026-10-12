import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { type ActivityDetail, api, type Streams } from "../api";
import { BarChart, LineChart, robustDomain } from "../charts";
import { activityLabel, CATEGORIES, duration, km, MULTISPORT_LABELS, speedLabel, swimPace } from "../format";
import { Alert, Card, Checkbox, errorMessage, Spinner } from "../ui";

const PACE_STEPS = [5, 10, 15, 20, 30, 60, 120, 300];

function mmss(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

type Series = {
  key: string;
  label: string;
  y: (number | null)[];
  format: (v: number) => string;
  invert?: boolean;
  steps?: number[];
};

function seriesFor(detail: ActivityDetail, s: Streams): Series[] {
  const out: Series[] = [];
  const round = (v: number) => String(Math.round(v));
  if (s.speed && detail.category === "bike") {
    out.push({
      key: "speed",
      label: "Vitesse (km/h)",
      y: s.speed.map((v) => (v === null ? null : v * 3.6)),
      format: (v) => v.toLocaleString("fr-FR", { maximumFractionDigits: 1 }),
    });
  } else if (s.speed && detail.category === "swim") {
    // Eau libre : allure /100 m le long du parcours GPS.
    out.push({
      key: "pace",
      label: "Allure (/100 m, plus haut = plus rapide)",
      y: s.speed.map((v) => (v === null || v < 0.2 ? null : 100 / v)),
      format: mmss,
      invert: true,
      steps: PACE_STEPS,
    });
  } else if (s.speed) {
    // Allure : un arrêt (vitesse quasi nulle) est un trou, pas un pic.
    out.push({
      key: "pace",
      label: "Allure (/km, plus haut = plus rapide)",
      y: s.speed.map((v) => (v === null || v < 0.8 ? null : 1000 / v)),
      format: mmss,
      invert: true,
      steps: PACE_STEPS,
    });
  }
  if (s.hr) out.push({ key: "hr", label: "Fréquence cardiaque (bpm)", y: s.hr, format: round });
  if (s.altitude) out.push({ key: "altitude", label: "Altitude (m)", y: s.altitude, format: round });
  if (s.power) out.push({ key: "power", label: "Puissance (W)", y: s.power, format: round });
  if (s.cadence) {
    out.push({
      key: "cadence",
      label: detail.category === "run" ? "Cadence (pas/min)" : "Cadence (tr/min)",
      y: s.cadence,
      format: round,
    });
  }
  return out;
}

export function ActivityDetailPage() {
  const { activityId } = useParams();
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!activityId) return;
    api
      .activity(activityId)
      .then(setDetail)
      .catch((e) => setError(errorMessage(e)));
  }, [activityId]);

  if (error) return <Alert>{error}</Alert>;
  if (!detail) return <Spinner />;

  async function toggleHidden(hidden: boolean) {
    if (!detail) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.setActivityHidden(detail.id, hidden);
      setDetail({ ...detail, hidden });
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  const cat = detail.category;
  const s = detail.streams;
  const avgSpeed = detail.distance_m ? detail.distance_m / detail.duration_s : null;
  // Abscisse : la distance si le GPS l'a enregistrée (course, vélo, eau libre), sinon le temps.
  const byDistance = !!s?.distance && (cat === "run" || cat === "bike" || cat === "swim");
  let x: number[] = [];
  if (s) {
    if (byDistance && s.distance) {
      let last = 0;
      x = s.distance.map((d) => {
        last = d ?? last;
        return last / 1000;
      });
    } else {
      x = s.t.map((t) => t / 60);
    }
  }
  const formatX = !byDistance
    ? (v: number) => `${Math.round(v)} min`
    : cat === "swim"
      ? (v: number) => `${Math.round(v * 1000).toLocaleString("fr-FR")} m`
      : (v: number) => `${v.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} km`;
  const pool = detail.pool_length_m;
  const lengths = detail.lengths ?? [];
  const series = s ? seriesFor(detail, s) : [];
  const zonesTotal = detail.hr_zones?.reduce((n, z) => n + z.seconds, 0) ?? 0;
  const splits = detail.splits ?? [];
  const fastest = Math.max(0, ...splits.map((sp) => sp.distance_m / sp.duration_s));
  const splitsHaveHr = splits.some((sp) => sp.avg_hr !== null);

  return (
    <>
      <Link to={`/graphes?type=${cat}`} className="text-sm text-ink-2 underline">
        ← {CATEGORIES[cat].label} : graphes et activités
      </Link>

      <Card>
        <p className="text-sm text-ink-3 first-letter:uppercase">
          {new Date(detail.start_time).toLocaleString("fr-FR", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
        <h1 className="text-2xl font-semibold">
          <span aria-hidden>{CATEGORIES[cat].icon}</span> {activityLabel(detail)}
        </h1>
        {detail.multisport && (
          <nav
            aria-label="Disciplines de la séance"
            className="mt-2 flex flex-wrap items-center gap-1 text-sm"
          >
            <span className="text-ink-3">
              {MULTISPORT_LABELS[detail.multisport.kind]} {detail.multisport.position}/
              {detail.multisport.count} :
            </span>
            {detail.multisport.parts.map((part, i) => (
              <span key={part.id} className="flex items-center gap-1">
                {i > 0 && (
                  <span aria-hidden className="text-ink-3">
                    →
                  </span>
                )}
                {part.id === detail.id ? (
                  <span aria-current="page" className="rounded bg-surface-2 px-1.5 py-0.5 font-medium">
                    {CATEGORIES[part.category].icon} {CATEGORIES[part.category].label}
                  </span>
                ) : (
                  <Link
                    to={`/activites/${encodeURIComponent(part.id)}`}
                    className="rounded px-1.5 py-0.5 text-accent underline"
                  >
                    {CATEGORIES[part.category].icon} {CATEGORIES[part.category].label}
                  </Link>
                )}
              </span>
            ))}
          </nav>
        )}
        <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Tile label="Durée" value={duration(detail.duration_s)} />
          {detail.distance_m ? (
            <Tile
              label="Distance"
              value={
                cat === "swim"
                  ? `${Math.round(detail.distance_m).toLocaleString("fr-FR")} m`
                  : km(detail.distance_m / 1000)
              }
            />
          ) : null}
          {avgSpeed && cat !== "gym" ? (
            <Tile
              label={cat === "bike" ? "Vitesse moyenne" : "Allure moyenne"}
              value={speedLabel(cat, avgSpeed)}
            />
          ) : null}
          {detail.avg_hr ? (
            <Tile
              label="FC moy. / max"
              value={`${Math.round(detail.avg_hr)}${detail.max_hr ? ` / ${Math.round(detail.max_hr)}` : ""} bpm`}
            />
          ) : null}
          {cat === "swim" && (pool || detail.sub_sport === "open_water") ? (
            <Tile label="Bassin" value={pool ? `${pool} m` : "Eau libre"} />
          ) : null}
          {detail.elevation_gain_m && cat !== "swim" ? (
            <Tile label="Dénivelé +" value={`${Math.round(detail.elevation_gain_m)} m`} />
          ) : null}
          {detail.avg_power ? (
            <Tile label="Puissance moy." value={`${Math.round(detail.avg_power)} W`} />
          ) : null}
        </dl>
        <div className="mt-4 border-t border-border pt-3">
          <Checkbox
            label="Cacher cette activité"
            checked={detail.hidden}
            onChange={toggleHidden}
            disabled={saving}
          />
          <p className="text-xs text-ink-3">
            Elle n'apparaît plus dans les listes ni les graphes, sauf si vous affichez les activités cachées.
          </p>
          {saveError && (
            <div className="mt-2">
              <Alert>{saveError}</Alert>
            </div>
          )}
        </div>
      </Card>

      {detail.unavailable && <Alert kind="info">Courbes indisponibles : {detail.unavailable}.</Alert>}

      {series.length > 0 && (
        <Card title="Courbes">
          <div className="space-y-5">
            {series.map((serie) => (
              <LineChart
                key={serie.key}
                label={serie.label}
                x={x}
                y={serie.y}
                yDomain={robustDomain(serie.y) ?? undefined}
                ySteps={serie.steps}
                invert={serie.invert}
                formatX={formatX}
                formatY={serie.format}
                hover={hover}
                onHover={setHover}
                height={140}
              />
            ))}
          </div>
        </Card>
      )}

      {splits.length > 1 && (
        <Card title={{ bike: "Par tranche de 5 km", swim: "Par 100 m" }[cat as string] ?? "Par kilomètre"}>
          <table className="w-full text-sm tabular-nums">
            <thead className="text-left text-xs text-ink-3">
              <tr>
                <th className="py-1 font-normal">{cat === "swim" ? "m" : "Km"}</th>
                <th className="font-normal">Temps</th>
                <th className="font-normal">{cat === "bike" ? "Vitesse" : "Allure"}</th>
                <th className="w-1/3 font-normal">
                  <span className="sr-only">Comparaison</span>
                </th>
                {splitsHaveHr && <th className="text-right font-normal">FC</th>}
              </tr>
            </thead>
            <tbody>
              {splits.map((sp, i) => {
                const speed = sp.distance_m / sp.duration_s;
                const end = splits.slice(0, i + 1).reduce((n, x) => n + x.distance_m, 0) / 1000;
                return (
                  <tr key={end} className="border-t border-border">
                    <td className="py-1.5">
                      {cat === "swim"
                        ? Math.round(end * 1000).toLocaleString("fr-FR")
                        : end.toLocaleString("fr-FR", { maximumFractionDigits: 1 })}
                    </td>
                    <td>{mmss(sp.duration_s)}</td>
                    <td>{speedLabel(cat, speed)}</td>
                    <td>
                      {/* Longueur proportionnelle à la vitesse (depuis zéro) : plus long = plus rapide. */}
                      <div
                        className="h-2 rounded-r bg-[var(--series-1)]"
                        style={{ width: `${(speed / fastest) * 100}%` }}
                        aria-hidden
                      />
                    </td>
                    {splitsHaveHr && <td className="text-right">{sp.avg_hr ?? "—"}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {pool && lengths.length > 0 && (
        <Card title={`Bassin de ${pool} m · ${lengths.length} longueurs`}>
          {/* Allure ramenée aux 100 m : comparable entre un bassin de 25 m et un de 50 m. */}
          <BarChart
            label="Allure par longueur (/100 m)"
            values={lengths.map((t) => (t / pool) * 100)}
            labels={lengths.map((_, i) => `${Math.round((i + 1) * pool)}`)}
            formatY={mmss}
            ySteps={PACE_STEPS}
            tooltip={(i) => (
              <>
                <p className="text-ink-3">
                  Longueur {i + 1} · {Math.round((i + 1) * pool).toLocaleString("fr-FR")} m
                </p>
                <p className="font-medium tabular-nums">
                  {lengths[i].toLocaleString("fr-FR")} s · {swimPace((lengths[i] / pool) * 100)}
                </p>
              </>
            )}
          />
          <p className="mt-1 text-xs text-ink-3">
            Distance cumulée en mètres sous l'axe. Barre plus courte = plus rapide.
          </p>
        </Card>
      )}

      {zonesTotal > 0 && detail.hr_zones && (
        <Card title="Temps par zone cardiaque">
          <ul className="space-y-2">
            {detail.hr_zones.map((z) => (
              <li key={z.zone} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 text-sm">
                <span>
                  Z{z.zone}{" "}
                  <span className="text-xs text-ink-3">
                    {z.low === null ? `< ${z.high}` : z.high === null ? `≥ ${z.low}` : `${z.low}–${z.high}`}
                  </span>
                </span>
                <div className="h-3 rounded-r bg-surface-2">
                  <div
                    className="h-3 rounded-r bg-[var(--series-1)]"
                    style={{ width: `${(z.seconds / zonesTotal) * 100}%` }}
                  />
                </div>
                <span className="text-right whitespace-nowrap tabular-nums">
                  {mmss(z.seconds)}{" "}
                  <span className="text-xs text-ink-3">{Math.round((z.seconds / zonesTotal) * 100)} %</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-3">
            Zones en % de FC de réserve (FC repos et max réglées dans le Profil).
          </p>
        </Card>
      )}
    </>
  );
}
