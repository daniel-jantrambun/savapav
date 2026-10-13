import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, type GoalCheck } from "../api";
import { dayLabel, duration, pace, parseTime, raceTime, todayIso } from "../format";
import { Alert, Button, buttonClass, Card, errorMessage, Field } from "../ui";

const DISTANCES = [
  { label: "5 km", km: 5 },
  { label: "10 km", km: 10 },
  { label: "Semi", km: 21.1 },
  { label: "Marathon", km: 42.195 },
];

export function NewPlanPage() {
  const navigate = useNavigate();
  const [distance, setDistance] = useState(21.1);
  const [target, setTarget] = useState("1:45:00");
  const [raceDate, setRaceDate] = useState("");
  const [runs, setRuns] = useState(4);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const targetSeconds = parseTime(target);
  const [check, setCheck] = useState<GoalCheck | null>(null);

  // Objectif comparé au temps estimé du jour, après une courte pause dans la saisie.
  useEffect(() => {
    setCheck(null);
    if (!targetSeconds || !raceDate) return;
    const timer = setTimeout(() => {
      api
        .checkGoal(distance, targetSeconds, raceDate)
        .then(setCheck)
        .catch(() => {});
    }, 400);
    return () => clearTimeout(timer);
  }, [distance, targetSeconds, raceDate]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!targetSeconds) {
      setError("Temps visé illisible : utilisez h:mm:ss (1:45:00) ou mm:ss (45:00)");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const plan = await api.createPlan({
        distance_km: distance,
        target_time_s: targetSeconds,
        race_date: raceDate,
        runs_per_week: runs,
      });
      navigate(`/plans/${plan.id}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Card title="Préparer une course">
      <form onSubmit={submit} className="space-y-5">
        <fieldset>
          <legend className="mb-2 text-sm font-medium text-ink-2">Distance</legend>
          <div className="grid grid-cols-4 gap-2">
            {DISTANCES.map((d) => (
              <button
                key={d.km}
                type="button"
                aria-pressed={distance === d.km}
                onClick={() => setDistance(d.km)}
                className={`min-h-10 rounded-lg border text-sm ${
                  distance === d.km
                    ? "border-accent bg-accent text-accent-ink"
                    : "border-border bg-surface text-ink"
                }`}
              >
                {d.label}
              </button>
            ))}
          </div>
        </fieldset>

        <Field
          label="Temps visé"
          inputMode="numeric"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          hint={
            targetSeconds
              ? `${duration(targetSeconds)} → allure course ${pace(targetSeconds / distance)}`
              : "Format h:mm:ss ou mm:ss"
          }
          required
        />

        <Field
          label="Date de la course"
          type="date"
          min={todayIso()}
          value={raceDate}
          onChange={(e) => setRaceDate(e.target.value)}
          hint="Au moins 3 semaines ; idéalement 12 à 18 selon la distance."
          required
        />

        <fieldset>
          <legend className="mb-2 text-sm font-medium text-ink-2">Sorties par semaine</legend>
          <div className="grid grid-cols-4 gap-2">
            {[3, 4, 5, 6].map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={runs === n}
                onClick={() => setRuns(n)}
                className={`min-h-10 rounded-lg border text-sm ${
                  runs === n ? "border-accent bg-accent text-accent-ink" : "border-border bg-surface text-ink"
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </fieldset>

        {check && <GoalNote check={check} raceDate={raceDate} onUse={(s) => setTarget(raceTime(s))} />}

        {error && <Alert>{error}</Alert>}
        <div className="flex gap-2">
          <Button type="submit" disabled={busy}>
            {busy ? "Calcul…" : "Générer le plan"}
          </Button>
          <Link to="/plans/nouveau" className={buttonClass("secondary")}>
            Annuler
          </Link>
        </div>
      </form>
    </Card>
  );
}

function GoalNote({
  check,
  raceDate,
  onUse,
}: {
  check: GoalCheck;
  raceDate: string;
  onUse: (seconds: number) => void;
}) {
  if (check.verdict === null) {
    return check.scanning ? (
      <Alert kind="info">
        Analyse de vos courses en cours : la vérification de l'objectif arrive bientôt.
      </Alert>
    ) : null;
  }
  const today = raceTime(check.predicted_s);
  const reachable = raceTime(check.reachable_s);
  const by = dayLabel(raceDate, { day: "numeric", month: "long" });
  const text = {
    ambitious: `Objectif ambitieux : votre temps estimé aujourd'hui est ${today}. D'ici le ${by}, environ ${reachable} semble atteignable.`,
    realistic: `Objectif réaliste : estimé ${today} aujourd'hui, environ ${reachable} atteignable d'ici le ${by}.`,
    conservative: `Objectif prudent : vous valez déjà environ ${today}. Vous pourriez viser plus vite.`,
  }[check.verdict];
  return (
    <div className="space-y-2">
      <Alert kind={check.verdict === "realistic" ? "success" : "info"}>
        <span aria-hidden>
          {check.verdict === "ambitious" ? "▲ " : check.verdict === "realistic" ? "● " : "▼ "}
        </span>
        {text}
      </Alert>
      <p className="text-xs text-ink-3">
        Estimation d'après votre {Math.round(check.source.distance_m / 1000)} km du{" "}
        {dayLabel(check.source.date, { day: "numeric", month: "short", year: "numeric" })}
        {check.error !== null && ` (± ${Math.round(check.error * 1000) / 10} %)`}, avec ~
        {Math.round(check.gain * 100)} % de progression possible d'ici la course.
      </p>
      {check.verdict !== "realistic" && (
        <Button variant="secondary" onClick={() => onUse(check.reachable_s)}>
          Viser {reachable}
        </Button>
      )}
    </div>
  );
}
