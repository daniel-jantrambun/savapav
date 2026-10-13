import argparse
import getpass
import json
import sys
from datetime import date
from pathlib import Path

from coach import auth, efforts, service
from coach.config import load_settings
from coach.db import connect
from coach.multisport import AXES, Preferences
from coach.planner import Goal


def _parse_time(value: str) -> int:
    """"1:45:00" ou "45:00" → secondes."""
    seconds = 0
    for part in value.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def _ask_password() -> str:
    password = getpass.getpass("Mot de passe : ")
    if password != getpass.getpass("Confirmation : "):
        sys.exit("Les mots de passe ne correspondent pas")
    return password


def _user_id(conn, username: str) -> int:
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        sys.exit(f"Utilisateur inconnu : {username}")
    return row["id"]


def main() -> None:
    parser = argparse.ArgumentParser(prog="coach")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-user", help="Crée un compte (aussi possible depuis l'app, page Comptes)")
    p.add_argument("username")
    p.add_argument("--name", help="nom affiché")
    p.add_argument("--admin", action="store_true", help="donne les droits d'administration")

    p = sub.add_parser("set-password", help="Réinitialise le mot de passe d'un compte")
    p.add_argument("username")

    sub.add_parser("list-users")

    p = sub.add_parser("import-fit", help="Importe les FIT (ou .zip, export Garmin compris) d'un dossier")
    p.add_argument("--user", required=True)
    p.add_argument("path", type=Path)

    p = sub.add_parser("sync-garmin", help="Synchronise Garmin Connect (pour le cron : --all)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--user")
    group.add_argument("--all", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--full", action="store_true", help="tout l'historique (long : pauses entre téléchargements)")

    p = sub.add_parser("reimport", help="Relit les FIT déjà stockés (uploads, Garmin) : remplit les nouveaux champs")
    p.add_argument("--user", required=True)

    sub.add_parser("garmin-check", help="Teste l'accès aux serveurs Garmin depuis cette machine / ce conteneur")

    p = sub.add_parser("plan", help="Génère un plan vers un objectif")
    p.add_argument("--user", required=True)
    p.add_argument("--distance", type=float, required=True, help="km, ex. 21.1")
    p.add_argument("--target", type=_parse_time, required=True, help="ex. 1:45:00")
    p.add_argument("--race-date", type=date.fromisoformat, required=True)
    p.add_argument("--runs", type=int, default=4)

    p = sub.add_parser("analyze", help="Ce que vous faites, sport par sport (8 dernières semaines)")
    p.add_argument("--user", required=True)

    p = sub.add_parser("performance", help="Temps estimés (5 km → marathon) ; analyse d'abord les nouvelles courses")
    p.add_argument("--user", required=True)

    p = sub.add_parser("block", help="Génère un bloc multisport de 4 semaines, sans objectif chiffré")
    p.add_argument("--user", required=True)
    p.add_argument("--sessions", type=int, default=5, help="séances par semaine, tous sports confondus")
    for sport, axes in AXES.items():
        p.add_argument(f"--{sport}", choices=axes, help="axe d'amélioration (sport non planifié si absent)")

    p = sub.add_parser("narrate", help="Fait rédiger un plan par le LLM local")
    p.add_argument("--user", required=True)
    p.add_argument("plan_id", type=int)
    p.add_argument("--week", type=int, action="append", help="index de semaine (0 = première)")

    args = parser.parse_args()
    settings = load_settings()
    conn = connect(settings.db_path)

    if args.command == "add-user":
        try:
            auth.create_user(conn, args.username, _ask_password(), args.name, is_admin=args.admin)
        except (ValueError, auth.AccountError) as e:
            sys.exit(str(e))
        print(f"Compte {args.username} créé")
    elif args.command == "set-password":
        try:
            auth.set_password(conn, args.username, _ask_password())
        except (ValueError, KeyError) as e:
            sys.exit(f"Échec : {e}")
        print("Mot de passe modifié, sessions existantes fermées")
    elif args.command == "list-users":
        for r in conn.execute(
            "SELECT u.username, u.display_name, g.connected_at, g.last_sync_at, g.last_error FROM users u "
            "LEFT JOIN garmin_accounts g ON g.user_id = u.id ORDER BY u.username"
        ):
            garmin_info = f"Garmin : dernière synchro {r['last_sync_at'] or 'jamais'}" if r["connected_at"] else ""
            print(f"{r['username']:<16} {r['display_name']:<20} {garmin_info} {r['last_error'] or ''}")
    elif args.command == "import-fit":
        from coach.ingest.fit_files import import_directory

        imported, skipped = import_directory(conn, _user_id(conn, args.user), args.path)
        print(f"{imported} activités importées, {skipped} fichiers ignorés")
    elif args.command == "sync-garmin":
        from coach.ingest import garmin

        if args.all:
            user_ids = [r["user_id"] for r in conn.execute("SELECT user_id FROM garmin_accounts")]
        else:
            user_ids = [_user_id(conn, args.user)]
        failed = False
        for user_id in user_ids:
            try:
                imported, skipped = garmin.sync(conn, user_id, settings.fit_dir, settings.secret_key, args.limit,
                                                full=args.full)
                print(f"utilisateur {user_id} : {imported} activités importées, {skipped} ignorées")
            except garmin.GarminError as e:
                # Un compte en erreur (jetons expirés...) ne bloque pas la synchro des autres.
                print(f"utilisateur {user_id} : {e}", file=sys.stderr)
                failed = True
        sys.exit(1 if failed else 0)
    elif args.command == "reimport":
        from coach.ingest.fit_files import import_directory

        user_id = _user_id(conn, args.user)
        imported, skipped = import_directory(conn, user_id, settings.fit_dir / str(user_id))
        print(f"{imported} activités relues, {skipped} fichiers ignorés")
    elif args.command == "garmin-check":
        from coach.ingest import garmin

        results = garmin.check_connectivity()
        for host, result in results:
            print(f"{host:<24} {result}")
        sys.exit(1 if any(r.startswith("ÉCHEC") for _, r in results) else 0)
    elif args.command == "plan":
        goal = Goal(args.distance, args.target, args.race_date, args.runs)
        plan_id, plan = service.create_plan(conn, _user_id(conn, args.user), goal, date.today())
        for w in plan["weeks"]:
            kinds = ", ".join(f"{s['kind']} {s['distance_km']}" for s in w["sessions"])
            print(f"S{w['index'] + 1:>2} {w['start']} {w['phase']:<6}{' (décharge)' if w['deload'] else '':<11}"
                  f"{w['volume_km']:>5} km  {kinds}")
        for warning in plan["warnings"]:
            print("⚠", warning)
        print(f"Plan #{plan_id} enregistré")
    elif args.command == "analyze":
        user = auth.user_for_id(conn, _user_id(conn, args.user))
        print(json.dumps(service.analysis(conn, user, date.today()), ensure_ascii=False, indent=2))
    elif args.command == "performance":
        user = auth.user_for_id(conn, _user_id(conn, args.user))
        scanned = efforts.scan_pending(conn, user["id"])
        print(f"{scanned} course(s) analysée(s)", file=sys.stderr)
        summary = service.performance_summary(conn, user, date.today())
        summary.pop("curve")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "block":
        user = auth.user_for_id(conn, _user_id(conn, args.user))
        focus = {sport: getattr(args, sport) for sport in AXES if getattr(args, sport)}
        try:
            plan_id, plan = service.create_block(conn, user, Preferences(focus, args.sessions), date.today())
        except (ValueError, service.InvalidPlan) as e:
            sys.exit(str(e))
        for w in plan["weeks"]:
            sessions = ", ".join(f"{s['date'][5:]} {s['sport']} {s['kind']} {s['duration_min']}'"
                                 for s in w["sessions"])
            print(f"S{w['index'] + 1} {w['start']}{' (décharge)' if w['deload'] else '':<11} {w['minutes']:>4} min  "
                  f"{sessions}")
        for warning in plan["warnings"]:
            print("⚠", warning)
        print(f"Bloc #{plan['number']} enregistré (plan {plan_id})")
    elif args.command == "narrate":
        user_id = _user_id(conn, args.user)
        count = service.narrate_plan(conn, settings, user_id, args.plan_id, args.week)
        plan = service.get_plan(conn, user_id, args.plan_id)
        print(json.dumps([w["text"] for w in plan["weeks"] if w["text"]], ensure_ascii=False, indent=2))
        print(f"{count} semaine(s) rédigée(s)")


if __name__ == "__main__":
    main()
