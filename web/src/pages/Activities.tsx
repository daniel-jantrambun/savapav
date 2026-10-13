import { type ChangeEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  type Activity,
  api,
  type GarminStatus,
  type ImportProgress,
  type LoadPoint,
  type SyncProgress,
} from "../api";
import { LoadChart } from "../LoadChart";
import { PerformanceCard } from "../Performance";
import {
  Alert,
  Button,
  buttonClass,
  Card,
  Checkbox,
  ConfirmDialog,
  errorMessage,
  Spinner,
  useShowHidden,
} from "../ui";
import { ActivityList } from "./Graphs";

const FULL_SYNC_POLL_MS = 3000;
const IMPORT_POLL_MS = 2000;
const PAGE_SIZE = 50;

const PHASE_LABELS: Record<NonNullable<SyncProgress["phase"]>, string> = {
  connexion: "Connexion à Garmin…",
  liste: "Recherche de vos activités chez Garmin…",
  telechargement: "Téléchargement",
  import: "Import des nouvelles activités…",
  termine: "Synchronisation complète terminée",
  erreur: "Synchronisation complète interrompue",
};

// Fraîcheur (TSB) : la couleur est doublée d'une icône et d'un libellé.
function freshness(tsb: number) {
  if (tsb > 5) return { label: "Frais", icon: "●", className: "text-good" };
  if (tsb >= -10) return { label: "Équilibré", icon: "●", className: "text-ink-2" };
  if (tsb >= -30) return { label: "En charge", icon: "▲", className: "text-warning" };
  return { label: "Fatigue élevée", icon: "■", className: "text-critical" };
}

export function ActivitiesPage() {
  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [load, setLoad] = useState<LoadPoint[] | null>(null);
  const [garmin, setGarmin] = useState<GarminStatus | null>(null);
  const [message, setMessage] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState<"import" | "sync" | null>(null);
  const [confirmFull, setConfirmFull] = useState(false);
  const [fullSync, setFullSync] = useState<SyncProgress | null>(null);
  const [importJob, setImportJob] = useState<ImportProgress | null>(null);
  const [showHidden, setShowHidden] = useShowHidden();

  const reload = useCallback(() => {
    // Une activité de plus que la page : indique s'il en reste à afficher.
    Promise.all([api.activities(PAGE_SIZE + 1, undefined, 0, showHidden), api.load(), api.garminStatus()])
      .then(([a, l, g]) => {
        setActivities(a.slice(0, PAGE_SIZE));
        setHasMore(a.length > PAGE_SIZE);
        setLoad(l);
        setGarmin(g);
      })
      .catch((e) => setMessage({ kind: "error", text: errorMessage(e) }));
  }, [showHidden]);

  useEffect(reload, [reload]);

  // Synchro complète en tâche de fond côté serveur : on suit son avancement (même après avoir quitté la page).
  useEffect(() => {
    api
      .garminSyncAllStatus()
      .then((p) => setFullSync(p.phase ? p : null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!fullSync?.running) return;
    const timer = setInterval(() => {
      api
        .garminSyncAllStatus()
        .then((p) => {
          setFullSync(p);
          if (!p.running) reload();
        })
        .catch(() => {});
    }, FULL_SYNC_POLL_MS);
    return () => clearInterval(timer);
  }, [fullSync?.running, reload]);

  // Import de fichier en tâche de fond côté serveur (un export complet prend plusieurs minutes).
  useEffect(() => {
    api
      .importStatus()
      .then((p) => setImportJob(p.running ? p : null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!importJob?.running) return;
    const timer = setInterval(() => {
      api
        .importStatus()
        .then((p) => {
          if (p.running) return setImportJob(p);
          setImportJob(null);
          setMessage(
            p.error
              ? { kind: "error", text: p.error }
              : {
                  kind: "success",
                  text: `${p.imported ?? 0} activité(s) importée(s)${p.skipped ? `, ${p.skipped} fichier(s) ignoré(s)` : ""}.`,
                },
          );
          reload();
        })
        .catch(() => {});
    }, IMPORT_POLL_MS);
    return () => clearInterval(timer);
  }, [importJob?.running, reload]);

  async function loadMore() {
    const shown = activities ?? [];
    setLoadingMore(true);
    try {
      const next = await api.activities(PAGE_SIZE + 1, undefined, shown.length, showHidden);
      setActivities([...shown, ...next.slice(0, PAGE_SIZE)]);
      setHasMore(next.length > PAGE_SIZE);
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    } finally {
      setLoadingMore(false);
    }
  }

  async function startFullSync() {
    setConfirmFull(false);
    setMessage(null);
    try {
      setFullSync(await api.garminSyncAll());
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    }
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy("import");
    setMessage(null);
    try {
      setImportJob(await api.importFile(file));
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  async function sync() {
    setBusy("sync");
    setMessage(null);
    try {
      const { imported } = await api.garminSync();
      setMessage({ kind: "success", text: `Synchro Garmin terminée : ${imported} activité(s) à jour.` });
      reload();
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  const today = load?.at(-1);
  const status = today ? freshness(today.tsb) : null;

  return (
    <>
      {message && <Alert kind={message.kind}>{message.text}</Alert>}

      <Card title="Forme et fatigue">
        {load === null ? (
          <Spinner />
        ) : load.length === 0 || !today || !status ? (
          <p className="text-sm text-ink-2">
            Importez vos activités (avec fréquence cardiaque) pour suivre votre charge d'entraînement.
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
              <div>
                <p className="text-xs text-ink-3">Fraîcheur du jour</p>
                <p className="text-3xl font-semibold tabular-nums">
                  {today.tsb > 0 ? "+" : ""}
                  {Math.round(today.tsb)}
                </p>
              </div>
              <p className={`pb-1 text-sm font-medium ${status.className}`}>
                <span aria-hidden>{status.icon}</span> {status.label}
              </p>
              <p className="pb-1 text-xs text-ink-3">
                = forme − fatigue. Négatif : vous accumulez de la charge.
              </p>
            </div>
            {load.length > 1 && <LoadChart data={load} />}
          </div>
        )}
      </Card>

      <PerformanceCard />

      <Card title="Ajouter des activités">
        <div className="flex flex-wrap gap-2">
          {garmin?.connected ? (
            <>
              <Button onClick={sync} disabled={busy !== null || fullSync?.running || importJob?.running}>
                {busy === "sync" ? "Synchro…" : "Synchroniser Garmin"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => setConfirmFull(true)}
                disabled={busy !== null || fullSync?.running || importJob?.running}
              >
                Tout synchroniser
              </Button>
            </>
          ) : (
            <Link to="/profil" className={buttonClass("primary")}>
              Connecter Garmin
            </Link>
          )}
          <label className={`${buttonClass("secondary")} cursor-pointer`}>
            {busy === "import" || importJob?.running ? "Import…" : "Importer un fichier"}
            <input
              type="file"
              accept=".fit,.zip"
              className="sr-only"
              onChange={importFile}
              disabled={busy !== null || importJob?.running}
            />
          </label>
        </div>
        <p className="mt-2 text-xs text-ink-3">
          « Synchroniser » récupère les 50 dernières activités Garmin ; « Tout synchroniser », tout
          l'historique. Fichier .fit, ou le .zip de l'export complet Garmin (Compte → Exporter vos données)
          tel quel.
        </p>
        {busy === "import" && (
          <div className="mt-4">
            <Spinner label="Envoi du fichier…" />
          </div>
        )}
        {importJob?.running && (
          <div className="mt-4" aria-live="polite">
            <Spinner
              label={`Import en cours : ${importJob.files ?? 0} fichier(s) lu(s), ${importJob.imported ?? 0} activité(s) importée(s)…`}
            />
          </div>
        )}
        {fullSync && <FullSyncStatus progress={fullSync} />}
      </Card>

      <ConfirmDialog
        open={confirmFull}
        title="Synchroniser tout l'historique Garmin ?"
        confirmLabel="Tout synchroniser"
        onConfirm={startFullSync}
        onCancel={() => setConfirmFull(false)}
      >
        <p>
          Toutes vos activités Garmin Connect seront téléchargées puis importées. Celles déjà présentes sont
          sautées.
        </p>
        <p>
          C'est long : une pause sépare chaque téléchargement pour ne pas être bloqué par Garmin, soit{" "}
          <strong className="text-ink">20 à 40 min pour 1 000 activités</strong>. La synchro continue sur le
          serveur si vous quittez cette page.
        </p>
        <p>
          Si Garmin limite les requêtes, la synchro s'arrête en gardant ce qui a été téléchargé : relancez-la
          plus tard, elle reprendra où elle s'était arrêtée.
        </p>
      </ConfirmDialog>

      <Card title="Dernières activités">
        <div className="-mt-2 mb-2">
          <Checkbox label="Afficher les activités cachées" checked={showHidden} onChange={setShowHidden} />
        </div>
        {activities === null ? (
          <Spinner />
        ) : activities.length === 0 ? (
          <p className="text-sm text-ink-2">Aucune activité pour l'instant.</p>
        ) : (
          <>
            <ActivityList activities={activities} />
            {hasMore && (
              <div className="mt-4 flex justify-center">
                <Button variant="secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Chargement…" : "Afficher plus d'activités"}
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  );
}

function FullSyncStatus({ progress }: { progress: SyncProgress }) {
  const phase = progress.phase ?? "connexion";
  const total = progress.to_download ?? 0;
  const done = progress.downloaded ?? 0;
  return (
    <div className="mt-4 space-y-2 rounded-lg border border-border px-3 py-2 text-sm" aria-live="polite">
      <p className="font-medium">
        {PHASE_LABELS[phase]}
        {phase === "telechargement" && total > 0 && ` : ${done} / ${total}`}
      </p>
      {phase === "liste" && <p className="text-xs text-ink-3">{progress.found ?? 0} activités trouvées</p>}
      {phase === "telechargement" && total > 0 && (
        <>
          <div
            className="h-2 rounded-full bg-surface-2"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            aria-label="Téléchargement des activités"
          >
            <div className="h-2 rounded-full bg-accent" style={{ width: `${(done / total) * 100}%` }} />
          </div>
          <p className="text-xs text-ink-3">
            {progress.found} activités chez Garmin, {total} à télécharger. Encore ~
            {Math.max(1, Math.round(((total - done) * 2) / 60))} min.
          </p>
        </>
      )}
      {phase === "termine" && (
        <p className="text-xs text-ink-3">
          {progress.found} activités chez Garmin, {progress.downloaded} téléchargées, {progress.imported}{" "}
          importées.
        </p>
      )}
      {phase === "erreur" && progress.error && <Alert>{progress.error}</Alert>}
    </div>
  );
}
