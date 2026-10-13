import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type Performance } from "./api";
import { LineChart } from "./charts";
import { dayLabel, pace, RACE_LABELS, raceTime } from "./format";
import { Alert, Button, Card, errorMessage, Spinner } from "./ui";

const SCAN_POLL_MS = 3000;
const DAY_MS = 86_400_000;

// Abscisse en jours, calculée en UTC dans les deux sens : sinon le 1er janvier s'affiche « déc. ».
const dayNumber = (day: string) => Date.parse(`${day}T00:00:00Z`) / DAY_MS;
const fromDayNumber = (value: number) => new Date(Math.round(value) * DAY_MS).toISOString().slice(0, 10);

const percent = (value: number) =>
  `${(value * 100).toLocaleString("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} %`;

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
      <dd className="text-xs text-ink-3 tabular-nums">{sub}</dd>
    </div>
  );
}

/** Temps estimés du jour (5 km → marathon), méthode retenue, courbe de progression et records. */
export function PerformanceCard() {
  const [data, setData] = useState<Performance | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .performance()
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  }, []);

  async function rescan() {
    setError(null);
    try {
      setData(await api.rescanPerformance());
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  // Premier passage : les meilleurs efforts de tout l'historique sont calculés en tâche de fond.
  const scanning = data?.status === "scanning";
  useEffect(() => {
    if (!scanning) return;
    const timer = setInterval(() => {
      api
        .performance()
        .then(setData)
        .catch(() => {});
    }, SCAN_POLL_MS);
    return () => clearInterval(timer);
  }, [scanning]);

  return (
    <Card title="Temps estimés">
      {error ? (
        <Alert>{error}</Alert>
      ) : data === null ? (
        <Spinner />
      ) : data.status === "scanning" ? (
        <div aria-live="polite">
          <Spinner
            label={`Analyse de vos courses : ${data.scan.done} / ${data.scan.total}… (quelques minutes la première fois)`}
          />
        </div>
      ) : (
        <>
          <PerformanceBody data={data} />
          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-3">
            <Button variant="secondary" onClick={rescan}>
              Recalculer les records
            </Button>
            <p className="text-xs text-ink-3">
              Relit toutes vos courses (quelques minutes), par exemple après avoir caché une activité
              douteuse.
            </p>
          </div>
        </>
      )}
    </Card>
  );
}

function PerformanceBody({ data }: { data: Extract<Performance, { status: "ready" }> }) {
  const [hover, setHover] = useState<number | null>(null);
  const { predictions } = data;
  const source = predictions[0]?.source;

  if (data.records.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        Aucune course avec GPS pour l'instant : importez vos sorties pour obtenir des temps estimés.
      </p>
    );
  }

  const curve = data.curve.filter((p) => p.time_s !== null);
  const x = data.curve.map((p) => dayNumber(p.day));
  const years = [...new Set(data.curve.map((p) => p.day.slice(0, 4)))];
  const everyYears = Math.max(1, Math.ceil(years.length / 6));
  const xTicks = years
    .filter((_, i) => i % everyYears === 0)
    .map((y) => dayNumber(`${y}-01-01`))
    .filter((t) => t >= x[0] && t <= x[x.length - 1]);

  return (
    <div className="space-y-4">
      {data.scan_error && <Alert>{data.scan_error}</Alert>}
      {predictions.length === 0 ? (
        <p className="text-sm text-ink-2">
          Aucun effort de 5 km ou plus ces 6 derniers mois : courez un 5 km (ou plus) à fond pour obtenir une
          estimation.
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {predictions.map((p) => (
              <Tile
                key={p.distance_m}
                label={RACE_LABELS[p.distance_m]}
                value={raceTime(p.time_s)}
                sub={pace(p.time_s / (p.distance_m / 1000))}
              />
            ))}
          </dl>
          <div className="space-y-1 text-xs text-ink-3">
            {source && (
              <p>
                D'après votre{" "}
                <Link
                  to={`/activites/${encodeURIComponent(source.activity_id)}`}
                  className="text-accent underline"
                >
                  {RACE_LABELS[source.distance_m]} du{" "}
                  {dayLabel(source.date, { day: "numeric", month: "short", year: "numeric" })}
                </Link>{" "}
                en {raceTime(source.duration_s)}
                {data.error !== null && <> · marge d'erreur mesurée ± {percent(data.error)}</>}. Au-delà du
                semi, l'estimation suppose l'endurance qui va avec.
              </p>
            )}
            <MethodNote data={data} />
          </div>
        </>
      )}

      {curve.length > 1 && (
        <LineChart
          label="Temps estimé sur 10 km, mois par mois (plus haut = plus rapide)"
          x={x}
          y={data.curve.map((p) => p.time_s)}
          xTicks={xTicks}
          invert
          formatX={(v) => dayLabel(fromDayNumber(v), { month: "short", year: "numeric" })}
          formatY={raceTime}
          hover={hover}
          onHover={setHover}
          height={180}
        />
      )}

      <details className="text-sm">
        <summary className="cursor-pointer text-ink-2">Records (meilleurs segments de vos sorties)</summary>
        <table className="mt-2 w-full text-left tabular-nums">
          <thead className="text-xs text-ink-3">
            <tr>
              <th className="py-1 font-normal">Distance</th>
              <th className="py-1 font-normal">Record</th>
              <th className="py-1 font-normal">12 derniers mois</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {data.records.map((r) => (
              <tr key={r.distance_m}>
                <td className="py-1.5">{RACE_LABELS[r.distance_m]}</td>
                {[r.all_time, r.last_year].map((best, i) => (
                  <td key={i === 0 ? "all" : "year"} className="py-1.5">
                    {best ? (
                      <Link
                        to={`/activites/${encodeURIComponent(best.activity_id)}`}
                        className="text-accent underline"
                      >
                        {raceTime(best.duration_s)}
                      </Link>
                    ) : (
                      "—"
                    )}
                    {best && (
                      <span className="ml-1 text-xs text-ink-3">
                        {dayLabel(best.date, { month: "short", year: "2-digit" })}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-ink-3">
          Segments les plus rapides mesurés au GPS (temps écoulé, pauses comprises), hors tapis et sauts du
          GPS.
        </p>
      </details>
    </div>
  );
}

function MethodNote({ data }: { data: Extract<Performance, { status: "ready" }> }) {
  const { evaluation: ev } = data;
  if (data.method === "xgboost" && ev.xgboost_error !== null && ev.baseline_error !== null) {
    return (
      <p>
        Méthode : XGBoost (charge, volume, efficacité cardiaque), {percent(ev.xgboost_error)} d'erreur contre{" "}
        {percent(ev.baseline_error)} pour la méthode simple, sur {ev.tested} efforts à fond non vus.
      </p>
    );
  }
  let why: string;
  if (!data.xgboost_available) why = "XGBoost n'est pas disponible sur ce serveur";
  else if (ev.xgboost_error === null)
    why = `pas encore assez d'efforts à fond (${ev.targets}) pour entraîner XGBoost`;
  else
    why = `XGBoost testé sur ${ev.tested} efforts à fond non vus : ${percent(ev.xgboost_error)} d'erreur contre ${percent(ev.baseline_error ?? 0)}, pas assez mieux pour être retenu`;
  return <p>Méthode simple : votre meilleur effort récent extrapolé (formule de Riegel) — {why}.</p>;
}
