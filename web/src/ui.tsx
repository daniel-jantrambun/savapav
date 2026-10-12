import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

export function Card({
  title,
  action,
  children,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-surface p-4 sm:p-5">
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

type Variant = "primary" | "secondary" | "danger";

/** Classes d'un bouton, aussi pour un lien ou un label stylé en bouton (jamais de bouton dans un lien). */
export function buttonClass(variant: Variant = "primary"): string {
  const styles = {
    primary: "bg-accent text-accent-ink hover:opacity-90",
    secondary: "border border-border bg-surface text-ink hover:bg-surface-2",
    danger: "border border-border bg-surface text-critical hover:bg-surface-2",
  }[variant];
  return `inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles}`;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant };

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return <button type="button" className={`${buttonClass(variant)} ${className}`} {...props} />;
}

export function Field({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-ink-2">{label}</span>
      <input
        className="block min-h-10 w-full rounded-lg border border-border bg-surface px-3 text-base text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/30"
        {...props}
      />
      {hint && <span className="mt-1 block text-xs text-ink-3">{hint}</span>}
    </label>
  );
}

export function Alert({
  kind = "error",
  children,
}: {
  kind?: "error" | "info" | "success";
  children: ReactNode;
}) {
  const styles = {
    error: "border-critical/40 text-critical",
    info: "border-border text-ink-2",
    success: "border-good/40 text-good",
  }[kind];
  return (
    <p
      role={kind === "error" ? "alert" : "status"}
      className={`rounded-lg border bg-surface px-3 py-2 text-sm ${styles}`}
    >
      {children}
    </p>
  );
}

export function Spinner({ label = "Chargement…" }: { label?: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-ink-3" role="status">
      <span className="size-4 animate-spin rounded-full border-2 border-border border-t-accent" />
      {label}
    </p>
  );
}

/** Boîte de confirmation modale (<dialog> natif : focus piégé, Échap pour annuler). */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  onConfirm,
  onCancel,
  children,
}: {
  open: boolean;
  title: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onCancel}
      aria-labelledby="confirm-title"
      className="m-auto w-[min(28rem,calc(100%-2rem))] rounded-xl border border-border bg-surface p-5 text-ink backdrop:bg-[rgb(0_0_0/0.5)]"
    >
      <h2 id="confirm-title" className="text-base font-semibold">
        {title}
      </h2>
      <div className="mt-2 space-y-2 text-sm text-ink-2">{children}</div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {/* Le focus va sur « Annuler » : la touche Entrée ne lance rien par mégarde. */}
        <Button variant="secondary" onClick={onCancel} autoFocus>
          Annuler
        </Button>
        <Button onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </dialog>
  );
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Erreur inattendue";
}

const SHOW_HIDDEN_KEY = "savapav.showHidden";

/** Afficher ou non les activités cachées : même choix sur Activités et Graphes, retenu par le navigateur. */
export function useShowHidden() {
  const [show, setShow] = useState(() => {
    try {
      return localStorage.getItem(SHOW_HIDDEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const update = (next: boolean) => {
    setShow(next);
    try {
      localStorage.setItem(SHOW_HIDDEN_KEY, next ? "1" : "0");
    } catch {
      // stockage indisponible (navigation privée) : le choix vaut pour cette page seulement
    }
  };
  return [show, update] as const;
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex min-h-10 items-center gap-2 text-sm">
      <input
        type="checkbox"
        className="size-4 accent-[var(--accent)]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      {label}
    </label>
  );
}
