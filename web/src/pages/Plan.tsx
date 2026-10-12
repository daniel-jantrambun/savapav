import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import {
  api,
  type BlockPlan,
  type BlockSession,
  type Plan,
  type PlanSummary,
  type RacePlan,
  type Session,
  type Sport,
} from "../api";
import {
  AXIS_LABELS,
  BLOCK_KIND_LABELS,
  dayLabel,
  distanceLabel,
  duration,
  GYM_FOCUS_LABELS,
  KIND_LABELS,
  km,
  minutes,
  PHASE_LABELS,
  pace,
  SPORT_ORDER,
  SPORTS,
  swimPace,
  todayIso,
} from "../format";
import { Markdown } from "../Markdown";
import { Alert, Button, buttonClass, Card, errorMessage, Spinner } from "../ui";

const NARRATE_POLL_MS = 10_000;

function planLabel(p: PlanSummary): string {
  if (p.goal.mode === "multisport") {
    return `Bloc ${p.goal.block} · dès le ${dayLabel(p.goal.start, { day: "numeric", month: "short", year: "numeric" })}`;
  }
  return `${distanceLabel(p.goal.distance_km)} · ${dayLabel(p.goal.race_date, { day: "numeric", month: "short", year: "numeric" })}`;
}

export function PlanPage() {
  const { planId } = useParams();
  const navigate = useNavigate();
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sans identifiant dans l'URL, on affiche le plan le plus récent.
  const selectedId = planId ? Number(planId) : plans?.[0]?.id;

  useEffect(() => {
    api
      .plans()
      .then(setPlans)
      .catch((e) => setError(errorMessage(e)));
  }, []);

  const reload = useCallback(() => {
    if (selectedId === undefined) return;
    api
      .plan(selectedId)
      .then(setPlan)
      .catch((e) => setError(errorMessage(e)));
  }, [selectedId]);

  useEffect(reload, [reload]);

  // Pendant que le LLM rédige (1 à 2 min par semaine sur le NAS), on rafraîchit régulièrement.
  useEffect(() => {
    if (!plan?.narrating) return;
    const timer = setInterval(reload, NARRATE_POLL_MS);
    return () => clearInterval(timer);
  }, [plan?.narrating, reload]);

  if (error) return <Alert>{error}</Alert>;
  if (plans === null || (selectedId !== undefined && plan === null)) return <Spinner />;

  if (plans.length === 0) {
    return (
      <Card>
        <div className="space-y-3 py-6 text-center">
          <p className="text-3xl" aria-hidden>
            🎯
          </p>
          <h1 className="text-lg font-semibold">Pas encore de plan</h1>
          <p className="text-sm text-ink-2">
            Dites ce que vous voulez améliorer dans chaque sport : le plan part de vos activités récentes.
          </p>
          <Link to="/plans/nouveau" className={buttonClass("primary")}>
            Créer mon plan
          </Link>
        </div>
      </Card>
    );
  }
  if (!plan) return null;

  async function narrate() {
    if (!plan) return;
    try {
      await api.narrate(plan.id);
      reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function remove() {
    if (!plan || !plans) return;
    const name = planLabel(plans.find((p) => p.id === plan.id) ?? { ...plan, goal: plan.goal });
    if (!window.confirm(`Supprimer le plan « ${name} » et ses textes rédigés ? C'est définitif.`)) return;
    try {
      await api.deletePlan(plan.id);
      // On revient au plan le plus récent restant (ou à l'écran « Pas encore de plan »).
      setPlans(plans.filter((p) => p.id !== plan.id));
      setPlan(null);
      navigate("/", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const today = todayIso();
  const narrated = plan.weeks.filter((w) => w.text).length;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {plans.length > 1 ? (
          <select
            aria-label="Choisir un plan"
            className="min-h-10 rounded-lg border border-border bg-surface px-2 text-sm"
            value={plan.id}
            onChange={(e) => navigate(`/plans/${e.target.value}`)}
          >
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {planLabel(p)}
              </option>
            ))}
          </select>
        ) : (
          <span />
        )}
        <Link to="/plans/nouveau" className={buttonClass("secondary")}>
          + Nouveau plan
        </Link>
      </div>

      {plan.mode === "multisport" ? (
        <BlockHeader key={plan.id} plan={plan} today={today} onError={setError} />
      ) : (
        <RaceHeader plan={plan} today={today} />
      )}

      <Card
        title="Le mot du coach"
        action={
          <Button variant="secondary" onClick={narrate} disabled={plan.narrating}>
            {plan.narrating ? "Rédaction…" : narrated ? "Réécrire" : "Rédiger le plan"}
          </Button>
        }
      >
        {plan.narrating ? (
          <Spinner
            label={`Le coach rédige les séances (${narrated}/${plan.weeks.length} semaines)… comptez 1 à 2 min par semaine.`}
          />
        ) : plan.narrate_error ? (
          <Alert>{plan.narrate_error}</Alert>
        ) : (
          <p className="text-sm text-ink-2">
            {narrated
              ? `${narrated} semaine${narrated > 1 ? "s" : ""} rédigée${narrated > 1 ? "s" : ""} par le LLM local. Les chiffres restent ceux du plan.`
              : "Le LLM local peut expliquer chaque séance en quelques phrases. Les chiffres du plan ne changent pas."}
          </p>
        )}
      </Card>

      <ol className="space-y-3">
        {plan.mode === "multisport"
          ? plan.weeks.map((week) => (
              <WeekCard
                key={week.index}
                index={week.index}
                start={week.start}
                total={plan.weeks.length}
                today={today}
                subtitle={week.deload ? "Décharge" : "Charge"}
                volume={minutes(week.minutes)}
                text={week.text}
              >
                {week.sessions.map((session) => (
                  <BlockSessionRow
                    key={session.date + session.sport + session.kind}
                    session={session}
                    today={today}
                  />
                ))}
              </WeekCard>
            ))
          : plan.weeks.map((week) => (
              <WeekCard
                key={week.index}
                index={week.index}
                start={week.start}
                total={plan.weeks.length}
                today={today}
                subtitle={PHASE_LABELS[week.phase] + (week.deload ? " · décharge" : "")}
                volume={km(week.volume_km)}
                text={week.text}
              >
                {week.sessions.map((session) => (
                  <SessionRow key={session.date + session.kind} session={session} today={today} />
                ))}
              </WeekCard>
            ))}
      </ol>

      <div className="flex justify-end">
        <Button variant="danger" onClick={remove} disabled={plan.narrating}>
          Supprimer ce plan
        </Button>
      </div>
    </>
  );
}

function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="mt-4 space-y-2">
      {warnings.map((w) => (
        <li key={w} className="flex gap-2 rounded-lg border border-warning/40 px-3 py-2 text-sm text-ink-2">
          <span aria-hidden className="text-warning">
            ⚠
          </span>
          <span>
            <span className="sr-only">Attention : </span>
            {w}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Reference({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function RaceHeader({ plan, today }: { plan: RacePlan; today: string }) {
  const weeksLeft = plan.weeks.filter((w) => w.start > today).length;
  return (
    <Card>
      <p className="text-sm text-ink-3">Objectif</p>
      <h1 className="text-2xl font-semibold">
        {distanceLabel(plan.goal.distance_km)} en {duration(plan.goal.target_time_s)}
      </h1>
      <p className="mt-1 text-sm text-ink-2">
        {dayLabel(plan.goal.race_date, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
        {weeksLeft > 0 && ` · dans ${weeksLeft} semaine${weeksLeft > 1 ? "s" : ""}`}
      </p>
      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Reference label="Allure course" value={pace(plan.paces.race)} />
        <Reference label="Seuil" value={pace(plan.paces.tempo)} />
        <Reference label="Fractionné" value={pace(plan.paces.intervals)} />
        <Reference label="Footing" value={pace(plan.paces.easy)} />
      </dl>
      <Warnings warnings={plan.warnings} />
    </Card>
  );
}

const PACE_SOURCES = {
  fc: "d'après vos sorties et votre fréquence cardiaque",
  allure_moyenne: "d'après l'allure moyenne de vos sorties",
  defaut: "valeurs par défaut, faute de sorties récentes",
};

function BlockHeader({
  plan,
  today,
  onError,
}: {
  plan: BlockPlan;
  today: string;
  onError: (e: string) => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const sports = SPORT_ORDER.filter((s) => plan.goal.focus[s]);
  const refs = plan.references;
  const end = addDays(plan.weeks[plan.weeks.length - 1].start, 6);
  const lastWeekStarted = plan.weeks[plan.weeks.length - 1].start <= today;

  async function nextBlock() {
    setBusy(true);
    try {
      const next = await api.createBlock({
        focus: plan.goal.focus,
        sessions_per_week: plan.goal.sessions_per_week,
      });
      navigate(`/plans/${next.id}`);
    } catch (e) {
      onError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Card>
      <p className="text-sm text-ink-3">
        Bloc {plan.number} · {plan.weeks.length} semaines
      </p>
      <h1 className="text-2xl font-semibold">
        Du {dayLabel(plan.weeks[0].start, { day: "numeric", month: "short" })} au{" "}
        {dayLabel(end, { day: "numeric", month: "short" })}
      </h1>
      <ul className="mt-3 flex flex-wrap gap-2">
        {sports.map((sport) => (
          <li key={sport} className="rounded-full border border-border px-3 py-1 text-sm">
            <span aria-hidden>{SPORTS[sport].icon}</span> {SPORTS[sport].label} ·{" "}
            {AXIS_LABELS[plan.goal.focus[sport] ?? "maintien"]}
            <span className="text-ink-3"> · {plan.sessions_per_sport[sport]}×/sem</span>
          </li>
        ))}
      </ul>
      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {plan.goal.focus.run && (
          <>
            <Reference label="Footing" value={pace(refs.run_paces.easy)} />
            <Reference label="Seuil" value={pace(refs.run_paces.tempo)} />
            <Reference label="Fractionné" value={pace(refs.run_paces.intervals)} />
          </>
        )}
        {plan.goal.focus.swim && <Reference label="Natation" value={swimPace(refs.swim_pace_s_per_100m)} />}
        {plan.goal.focus.bike && (
          <Reference
            label="Vélo endurance"
            value={`${refs.hr_zones.endurance[0]}–${refs.hr_zones.endurance[1]} bpm`}
          />
        )}
      </dl>
      {plan.goal.focus.run && (
        <p className="mt-2 text-xs text-ink-3">Allures course {PACE_SOURCES[refs.run_paces_source]}.</p>
      )}
      <Warnings warnings={plan.warnings} />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant={lastWeekStarted ? "primary" : "secondary"} onClick={nextBlock} disabled={busy}>
          {busy ? "Calcul…" : lastWeekStarted ? "Préparer le bloc suivant" : "Recalculer dès lundi"}
        </Button>
        <p className="text-xs text-ink-3">
          Recalculé à partir de ce que vous aurez réellement fait, avec les mêmes choix.
        </p>
      </div>
    </Card>
  );
}

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function WeekCard({
  index,
  start,
  total,
  today,
  subtitle,
  volume,
  text,
  children,
}: {
  index: number;
  start: string;
  total: number;
  today: string;
  subtitle: string;
  volume: string;
  text: string | null;
  children: ReactNode;
}) {
  const end = addDays(start, 6);
  const isCurrent = start <= today && today <= end;
  const isPast = end < today;
  const [open, setOpen] = useState(isCurrent || (index === 0 && start > today));

  return (
    <li
      className={`rounded-xl border bg-surface ${isCurrent ? "border-accent" : "border-border"} ${isPast ? "opacity-60" : ""}`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div>
          <p className="text-sm font-semibold">
            Semaine {index + 1}/{total}
            {isCurrent && (
              <span className="ml-2 rounded bg-accent px-1.5 py-0.5 text-xs text-accent-ink">en cours</span>
            )}
          </p>
          <p className="text-xs text-ink-3">
            {dayLabel(start, { day: "numeric", month: "short" })} –{" "}
            {dayLabel(end, { day: "numeric", month: "short" })} · {subtitle}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium tabular-nums">{volume}</span>
          <span aria-hidden className={`text-ink-3 transition ${open ? "rotate-180" : ""}`}>
            ▾
          </span>
        </div>
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-4 py-3">
          <ul className="divide-y divide-border">{children}</ul>
          {text && (
            <blockquote className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink-2">
              <Markdown text={text} />
            </blockquote>
          )}
        </div>
      )}
    </li>
  );
}

function SessionRow({ session, today }: { session: Session; today: string }) {
  const isToday = session.date === today;
  const detail =
    session.reps && session.rep_m
      ? `${session.reps} × ${session.rep_m >= 1000 ? `${session.rep_m / 1000} km` : `${session.rep_m} m`}` +
        (session.recovery_s ? `, récup ${session.recovery_s} s` : "")
      : null;
  return (
    <li className={`flex items-start justify-between gap-3 py-2 ${isToday ? "font-medium" : ""}`}>
      <div>
        <p className="text-sm">
          <span className="inline-block w-24 text-ink-3 first-letter:uppercase">
            {isToday ? "Aujourd'hui" : dayLabel(session.date)}
          </span>
          {KIND_LABELS[session.kind]}
        </p>
        {detail && <p className="ml-24 text-xs text-ink-3">{detail}</p>}
      </div>
      <div className="text-right text-sm tabular-nums">
        <p>{km(session.distance_km)}</p>
        <p className="text-xs text-ink-3">{pace(session.pace_s_per_km)}</p>
      </div>
    </li>
  );
}

function seconds(value: number): string {
  return value >= 60 && value % 60 === 0 ? `${value / 60}\u00a0min` : `${value}\u00a0s`;
}

function blockDetail(s: BlockSession): string | null {
  if (s.focus) return GYM_FOCUS_LABELS[s.focus];
  if (!s.reps) return null;
  const effort = s.rep_m
    ? s.rep_m >= 1000
      ? `${s.rep_m / 1000}\u00a0km`
      : `${s.rep_m}\u00a0m`
    : seconds(s.rep_s ?? 0);
  const extra = s.kind === "technique" ? " éducatifs" : "";
  return `${s.reps}\u00a0×\u00a0${effort}${extra}${s.recovery_s ? `, récup ${seconds(s.recovery_s)}` : ""}`;
}

function intensity(s: BlockSession): string | null {
  if (s.pace_s_per_km) return pace(s.pace_s_per_km);
  if (s.pace_s_per_100m) return swimPace(s.pace_s_per_100m);
  if (s.hr_low && s.hr_high) return `${s.hr_low}–${s.hr_high} bpm`;
  return null;
}

function distance(sport: Sport, value: number | null): string | null {
  if (!value) return null;
  return sport === "swim" ? `${Math.round(value * 1000).toLocaleString("fr-FR")} m` : km(value);
}

function BlockSessionRow({ session, today }: { session: BlockSession; today: string }) {
  const isToday = session.date === today;
  const detail = blockDetail(session);
  const sub = [distance(session.sport, session.distance_km), intensity(session)].filter(Boolean).join(" · ");
  return (
    <li className={`flex items-start justify-between gap-3 py-2 ${isToday ? "font-medium" : ""}`}>
      <div className="min-w-0">
        <p className="text-xs text-ink-3 first-letter:uppercase">
          {isToday ? "Aujourd'hui" : dayLabel(session.date)}
        </p>
        <p className="text-sm">
          <span aria-hidden>{SPORTS[session.sport].icon} </span>
          <span className="sr-only">{SPORTS[session.sport].label} : </span>
          {BLOCK_KIND_LABELS[session.kind]}
          {session.hard && (
            <span className="ml-2 rounded border border-warning/40 px-1 text-xs text-warning">intense</span>
          )}
        </p>
        {detail && <p className="text-xs text-ink-3">{detail}</p>}
      </div>
      <div className="shrink-0 text-right text-sm tabular-nums">
        <p>{minutes(session.duration_min)}</p>
        {sub && <p className="text-xs whitespace-nowrap text-ink-3">{sub}</p>}
      </div>
    </li>
  );
}
