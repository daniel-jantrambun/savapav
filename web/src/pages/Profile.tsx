import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type GarminStatus } from "../api";
import { useAuth, useUser } from "../auth";
import { Alert, Button, Card, ConfirmDialog, errorMessage, Field, Spinner } from "../ui";

type Message = { kind: "error" | "success"; text: string } | null;

export function ProfilePage() {
  const { logout } = useAuth();
  return (
    <>
      <ProfileCard />
      <GarminCard />
      <DataCard />
      <PasswordCard />
      <Button variant="danger" className="w-full sm:w-auto" onClick={logout}>
        Se déconnecter
      </Button>
    </>
  );
}

function ProfileCard() {
  const user = useUser();
  const { setUser } = useAuth();
  const [name, setName] = useState(user.display_name);
  const [hrRest, setHrRest] = useState(String(user.hr_rest));
  const [hrMax, setHrMax] = useState(String(user.hr_max));
  const [message, setMessage] = useState<Message>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      setUser(
        await api.updateMe({ display_name: name.trim(), hr_rest: Number(hrRest), hr_max: Number(hrMax) }),
      );
      setMessage({ kind: "success", text: "Profil enregistré." });
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    }
  }

  return (
    <Card title="Profil">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom affiché" value={name} onChange={(e) => setName(e.target.value)} required />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="FC de repos"
            type="number"
            inputMode="numeric"
            min={30}
            max={100}
            value={hrRest}
            onChange={(e) => setHrRest(e.target.value)}
            hint="bpm, mesurée au réveil"
          />
          <Field
            label="FC max"
            type="number"
            inputMode="numeric"
            min={120}
            max={230}
            value={hrMax}
            onChange={(e) => setHrMax(e.target.value)}
            hint="bpm, la plus haute observée"
          />
        </div>
        <p className="text-xs text-ink-3">
          Elles servent à calculer la charge de chaque séance à partir de votre fréquence cardiaque.
        </p>
        {message && <Alert kind={message.kind}>{message.text}</Alert>}
        <Button type="submit">Enregistrer</Button>
      </form>
    </Card>
  );
}

function GarminCard() {
  const [status, setStatus] = useState<GarminStatus | null>(null);
  const [step, setStep] = useState<"credentials" | "mfa">("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  const refresh = useCallback(() => api.garminStatus().then(setStatus), []);

  useEffect(() => {
    refresh().catch((e) => setMessage({ kind: "error", text: errorMessage(e) }));
  }, [refresh]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  const connect = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      const { status: result } = await api.garminConnect(email.trim(), password);
      // Le mot de passe ne reste pas en mémoire dans la page une fois envoyé.
      setPassword("");
      if (result === "needs_mfa") {
        setStep("mfa");
      } else {
        await refresh();
        setMessage({ kind: "success", text: "Compte Garmin connecté. Lancez une synchro depuis Activités." });
      }
    });
  };

  const submitMfa = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      await api.garminMfa(code.trim());
      setCode("");
      setStep("credentials");
      await refresh();
      setMessage({ kind: "success", text: "Compte Garmin connecté. Lancez une synchro depuis Activités." });
    });
  };

  const disconnect = () =>
    run(async () => {
      await api.garminDisconnect();
      await refresh();
    });

  return (
    <Card title="Garmin Connect">
      {status === null ? (
        <Spinner />
      ) : status.connected ? (
        <div className="space-y-3">
          <p className="text-sm">
            <span className="text-good" aria-hidden>
              ●
            </span>{" "}
            Connecté
            {status.last_sync_at &&
              ` · dernière synchro le ${new Date(status.last_sync_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}`}
          </p>
          {status.last_error && (
            <Alert>
              La dernière synchro a échoué. Si ça persiste, déconnectez puis reconnectez votre compte. (
              {status.last_error})
            </Alert>
          )}
          <Button variant="danger" onClick={disconnect} disabled={busy}>
            Déconnecter Garmin
          </Button>
        </div>
      ) : step === "mfa" ? (
        <form onSubmit={submitMfa} className="space-y-4">
          <p className="text-sm text-ink-2">
            Garmin vous a envoyé un code de vérification (email ou application).
          </p>
          <Field
            label="Code de vérification"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Vérification…" : "Valider"}
            </Button>
            <Button variant="secondary" onClick={() => setStep("credentials")} disabled={busy}>
              Annuler
            </Button>
          </div>
        </form>
      ) : (
        <form onSubmit={connect} className="space-y-4">
          <p className="text-sm text-ink-2">
            Votre mot de passe Garmin sert une seule fois à obtenir une autorisation d'accès. Il n'est jamais
            enregistré ; seule l'autorisation l'est, chiffrée.
          </p>
          <Field
            label="Email Garmin"
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Field
            label="Mot de passe Garmin"
            type="password"
            autoComplete="off"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <Button type="submit" disabled={busy}>
            {busy ? "Connexion à Garmin…" : "Connecter mon compte"}
          </Button>
        </form>
      )}
      {message && (
        <div className="mt-3">
          <Alert kind={message.kind}>{message.text}</Alert>
        </div>
      )}
    </Card>
  );
}

function DataCard() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function deleteActivities() {
    setConfirmDelete(false);
    setBusy(true);
    setMessage(null);
    try {
      const { deleted } = await api.deleteActivities();
      setMessage({ kind: "success", text: `${deleted} activité(s) supprimée(s) de l'application.` });
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Données">
      <div className="space-y-3">
        <p className="text-sm text-ink-2">
          Efface toutes vos activités de l'application, qu'elles viennent de Garmin ou d'un import de
          fichiers.
        </p>
        <Button variant="danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
          {busy ? "Suppression…" : "Supprimer mes activités"}
        </Button>
        {message && <Alert kind={message.kind}>{message.text}</Alert>}
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title="Supprimer toutes vos activités ?"
        confirmLabel="Supprimer"
        onConfirm={deleteActivities}
        onCancel={() => setConfirmDelete(false)}
      >
        <p>
          Toutes vos activités sont effacées de l'application : la liste, les graphes et la courbe
          forme/fatigue repartent de zéro. Vos plans sont conservés.
        </p>
        <p>
          Votre compte Garmin Connect n'est pas touché. Les fichiers déjà téléchargés restent sur le NAS : la
          prochaine synchro Garmin les réimportera.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<Message>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setMessage({ kind: "success", text: "Mot de passe modifié. Reconnectez-vous…" });
      // Le changement ferme toutes les sessions, celle-ci comprise : le prochain appel renvoie vers la connexion.
      setTimeout(() => api.me().catch(() => {}), 1500);
    } catch (e) {
      setMessage({ kind: "error", text: errorMessage(e) });
    }
  }

  return (
    <Card title="Mot de passe">
      <form onSubmit={submit} className="space-y-4">
        <Field
          label="Mot de passe actuel"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
        <Field
          label="Nouveau mot de passe"
          type="password"
          autoComplete="new-password"
          minLength={10}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          hint="10 caractères minimum. Vous devrez vous reconnecter."
          required
        />
        {message && <Alert kind={message.kind}>{message.text}</Alert>}
        <Button type="submit">Changer le mot de passe</Button>
      </form>
    </Card>
  );
}
