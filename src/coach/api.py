import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from coach import auth, efforts, service
from coach.config import Settings, load_settings
from coach.db import connect
from coach.ingest import garmin
from coach.ingest.fit_files import ImportProgress, import_directory
from coach.multisport import Preferences
from coach.planner import Goal

SESSION_COOKIE = "coach_session"


class LoginIn(BaseModel):
    username: str
    password: str


class NewUserIn(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    is_admin: bool = False


class ResetPasswordIn(BaseModel):
    new_password: str


class ProfileIn(BaseModel):
    display_name: str | None = None
    hr_rest: int | None = Field(default=None, ge=30, le=100)
    hr_max: int | None = Field(default=None, ge=120, le=230)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class GarminLoginIn(BaseModel):
    email: str
    password: str


class GarminMfaIn(BaseModel):
    code: str


class ActivityPatchIn(BaseModel):
    hidden: bool


class GoalIn(BaseModel):
    distance_km: float = Field(gt=0, le=100)
    target_time_s: int = Field(gt=0)
    race_date: date
    runs_per_week: int = Field(default=4, ge=3, le=6)


class BlockIn(BaseModel):
    focus: dict[str, str]  # sport (run | bike | swim | gym) → axe d'amélioration
    sessions_per_week: int = Field(default=5, ge=1, le=12)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Savapav", docs_url="/api/docs", openapi_url="/api/openapi.json")
    api = APIRouter(prefix="/api")
    # Plans en cours de rédaction par le LLM (tâches de fond du process), pour que l'interface puisse l'afficher.
    narrating: set[int] = set()
    narrate_errors: dict[int, str] = {}
    # Synchros Garmin complètes (tout l'historique), par utilisateur : longues, donc en tâche de fond.
    full_syncs: dict[int, garmin.SyncProgress] = {}
    file_imports: dict[int, ImportProgress] = {}
    effort_scans: dict[int, efforts.ScanProgress] = {}

    def db():
        # SQLite : une connexion par requête, simple et suffisant pour une famille.
        conn = connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[object, Depends(db)]

    def current_user(conn: Conn, coach_session: Annotated[str | None, Cookie()] = None) -> dict:
        user = auth.user_for_session(conn, coach_session) if coach_session else None
        if user is None:
            raise HTTPException(401, "Non connecté")
        return user

    User = Annotated[dict, Depends(current_user)]

    def admin_user(user: User) -> dict:
        if not user["is_admin"]:
            raise HTTPException(403, "Réservé aux administrateurs")
        return user

    Admin = Annotated[dict, Depends(admin_user)]

    def public_user(user: dict) -> dict:
        return {**{k: user[k] for k in ("id", "username", "display_name", "hr_rest", "hr_max")},
                "is_admin": bool(user["is_admin"])}

    def open_session(response: Response, conn, user_id: int) -> None:
        response.set_cookie(
            SESSION_COOKIE, auth.create_session(conn, user_id), max_age=auth.SESSION_DAYS * 86400,
            httponly=True, samesite="lax", secure=settings.cookie_secure,
        )

    @api.get("/health")
    def health():
        return {"status": "ok"}

    # --- Installation : création du premier compte (admin) ----------------------------------------------------

    @api.get("/setup")
    def setup_status(conn: Conn):
        return {"needs_setup": auth.needs_setup(conn)}

    @api.post("/setup", status_code=201)
    def setup(body: NewUserIn, response: Response, conn: Conn):
        """Possible uniquement tant qu'il n'existe aucun compte ; le compte créé est admin et connecté."""
        try:
            user_id = auth.create_first_admin(conn, body.username.strip(), body.password, body.display_name)
        except auth.AccountError as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        open_session(response, conn, user_id)
        return public_user(auth.user_for_id(conn, user_id))

    # --- Comptes ---------------------------------------------------------------------------------------------

    @api.post("/login")
    def login(body: LoginIn, response: Response, conn: Conn):
        user = auth.authenticate(conn, body.username, body.password)
        if user is None:
            raise HTTPException(401, "Identifiants invalides")
        open_session(response, conn, user["id"])
        return public_user(user)

    @api.post("/logout", status_code=204)
    def logout(response: Response, conn: Conn, coach_session: Annotated[str | None, Cookie()] = None):
        if coach_session:
            auth.delete_session(conn, coach_session)
        response.delete_cookie(SESSION_COOKIE)

    @api.get("/me")
    def me(user: User):
        return public_user(user)

    @api.patch("/me")
    def update_me(body: ProfileIn, user: User, conn: Conn):
        service.update_profile(conn, user["id"], **body.model_dump())
        return public_user(auth.user_for_id(conn, user["id"]))

    @api.post("/me/password", status_code=204)
    def change_password(body: PasswordIn, user: User, conn: Conn):
        if not auth.verify_password(body.current_password, user["password_hash"]):
            raise HTTPException(403, "Mot de passe actuel incorrect")
        try:
            auth.set_password(conn, user["username"], body.new_password)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    # --- Administration des comptes (admins uniquement) -------------------------------------------------------

    @api.get("/admin/users")
    def admin_list_users(_: Admin, conn: Conn):
        return auth.list_users(conn)

    @api.post("/admin/users", status_code=201)
    def admin_create_user(body: NewUserIn, _: Admin, conn: Conn):
        try:
            user_id = auth.create_user(conn, body.username.strip(), body.password, body.display_name, body.is_admin)
        except auth.AccountError as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return public_user(auth.user_for_id(conn, user_id))

    @api.delete("/admin/users/{user_id}", status_code=204)
    def admin_delete_user(user_id: int, admin: Admin, conn: Conn):
        try:
            auth.delete_user(conn, user_id, acting_user_id=admin["id"])
        except KeyError as e:
            raise HTTPException(404) from e
        except auth.AccountError as e:
            raise HTTPException(409, str(e)) from e
        # Les données en base partent en cascade ; restent les fichiers FIT importés.
        shutil.rmtree(settings.fit_dir / str(user_id), ignore_errors=True)

    @api.post("/admin/users/{user_id}/password", status_code=204)
    def admin_reset_password(user_id: int, body: ResetPasswordIn, _: Admin, conn: Conn):
        try:
            auth.set_password_for_id(conn, user_id, body.new_password)
        except KeyError as e:
            raise HTTPException(404) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    # --- Garmin ----------------------------------------------------------------------------------------------

    @api.get("/me/garmin")
    def garmin_status(user: User, conn: Conn):
        return garmin.status(conn, user["id"])

    @api.post("/me/garmin")
    def garmin_connect(body: GarminLoginIn, user: User, conn: Conn):
        """Le mot de passe Garmin sert uniquement à obtenir des jetons ; il n'est jamais stocké."""
        try:
            return {"status": garmin.start_login(conn, user["id"], body.email, body.password, settings.secret_key)}
        except garmin.GarminError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/me/garmin/mfa")
    def garmin_mfa(body: GarminMfaIn, user: User, conn: Conn):
        try:
            garmin.complete_mfa(conn, user["id"], body.code, settings.secret_key)
        except garmin.GarminError as e:
            raise HTTPException(400, str(e)) from e
        return {"status": "connected"}

    @api.delete("/me/garmin", status_code=204)
    def garmin_disconnect(user: User, conn: Conn):
        garmin.disconnect(conn, user["id"])

    @api.post("/me/garmin/sync")
    def garmin_sync(user: User, conn: Conn, limit: int = 50):
        if (current := full_syncs.get(user["id"])) and current.running:
            raise HTTPException(409, "Synchronisation complète en cours")
        try:
            imported, skipped = garmin.sync(conn, user["id"], settings.fit_dir, settings.secret_key, limit)
        except garmin.GarminError as e:
            raise HTTPException(502, str(e)) from e
        return {"imported": imported, "skipped": skipped}

    @api.get("/me/garmin/sync-all")
    def garmin_sync_all_status(user: User):
        progress = full_syncs.get(user["id"])
        return progress.to_dict() if progress else {"phase": None, "running": False}

    @api.post("/me/garmin/sync-all", status_code=202)
    def garmin_sync_all(user: User, conn: Conn, background: BackgroundTasks):
        """Tout l'historique Garmin, en tâche de fond (20 à 40 min pour 1 000 activités) : suivi par GET."""
        if not garmin.status(conn, user["id"])["connected"]:
            raise HTTPException(400, "Compte Garmin non connecté")
        if (current := full_syncs.get(user["id"])) and current.running:
            raise HTTPException(409, "Synchronisation complète déjà en cours")
        progress = full_syncs[user["id"]] = garmin.SyncProgress()

        def run():
            bg_conn = connect(settings.db_path)
            try:
                garmin.sync(bg_conn, user["id"], settings.fit_dir, settings.secret_key, full=True, progress=progress)
            except Exception as e:  # déjà noté dans `progress` par la synchro, sauf erreur avant son démarrage
                if progress.running:
                    progress.finish(str(e))
            finally:
                bg_conn.close()

        background.add_task(run)
        return progress.to_dict()

    # --- Activités et charge ---------------------------------------------------------------------------------

    @api.get("/activities")
    def list_activities(user: User, conn: Conn, limit: int = 50, category: str | None = None,
                        offset: int = Query(default=0, ge=0), hidden: bool = False):
        # hidden : inclure les activités masquées.
        return service.activities_with_category(conn, user["id"], category, limit, offset, hidden)

    @api.get("/activities/category")
    def list_activities_by_category(user: User, conn: Conn, limit: int = 50, category: str | None = None,
                                    weeks: int = 26, hidden: bool = False):
        return service.activities_with_category_and_weeks(conn, user["id"], category, weeks, limit, hidden)

    @api.delete("/activities")
    def delete_activities(user: User, conn: Conn):
        running = [p for p in (file_imports.get(user["id"]), full_syncs.get(user["id"])) if p and p.running]
        if running:
            raise HTTPException(409, "Import ou synchronisation en cours : réessayez une fois terminé")
        return {"deleted": service.delete_activities(conn, user["id"])}

    # Avant /activities/{activity_id}, qui capterait sinon « import ».
    @api.get("/activities/import")
    def import_file_status(user: User):
        progress = file_imports.get(user["id"])
        return progress.to_dict() if progress else {"running": False}

    @api.post("/activities/import", status_code=202)
    def import_file(file: UploadFile, user: User, background: BackgroundTasks):
        """Import d'un .fit, ou d'un .zip (export complet Garmin compris), en tâche de fond : suivi par GET.
        Un export complet prend plusieurs minutes, au-delà du délai du reverse proxy (504 de Web Station)."""
        name = Path(file.filename or "upload").name
        if not name.lower().endswith((".fit", ".zip")):
            raise HTTPException(422, "Fichier .fit ou .zip attendu")
        if (current := file_imports.get(user["id"])) and current.running:
            raise HTTPException(409, "Import déjà en cours")
        target = settings.fit_dir / str(user["id"]) / "uploads" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        target.mkdir(parents=True)
        with (target / name).open("wb") as out:
            shutil.copyfileobj(file.file, out)
        progress = file_imports[user["id"]] = ImportProgress()

        def run():
            bg_conn = connect(settings.db_path)
            try:
                import_directory(bg_conn, user["id"], target, progress=progress)
                progress.finish()
            except Exception as e:
                progress.finish(f"Import interrompu : {e}")
            finally:
                bg_conn.close()

        background.add_task(run)
        return progress.to_dict()

    @api.get("/activities/{activity_id}")
    def get_activity(activity_id: str, user: User, conn: Conn):
        detail = service.activity_detail(conn, user, activity_id)
        if detail is None:
            raise HTTPException(404)
        return detail

    @api.patch("/activities/{activity_id}")
    def update_activity(activity_id: str, body: ActivityPatchIn, user: User, conn: Conn):
        if not service.set_hidden(conn, user["id"], activity_id, body.hidden):
            raise HTTPException(404)
        return {"hidden": body.hidden}

    @api.get("/stats")
    def stats(user: User, conn: Conn, weeks: int = Query(default=26, ge=0), hidden: bool = False,
              by: Literal["auto", "week", "month"] = "auto"):
        # 0 : tout l'historique.
        if 0 < weeks < 4:
            raise HTTPException(422, "Au moins 4 semaines")
        # by : regroupement par semaine ou par mois ; auto = par mois au-delà de 5 ans.
        monthly = None if by == "auto" else by == "month"
        return service.stats(conn, user, date.today(), weeks, hidden, monthly)

    @api.get("/load")
    def load(user: User, conn: Conn, days: int = 120):
        return service.load_series(conn, user, until=date.today())[-days:]

    @api.get("/analysis")
    def analysis(user: User, conn: Conn):
        return service.analysis(conn, user, date.today())

    # --- Prédiction de performance -----------------------------------------------------------------------

    def scanning(user: dict, conn, background: BackgroundTasks) -> dict | None:
        """Analyse des meilleurs efforts en cours, ou lancée s'il reste des courses à analyser (tâche de fond :
        tout un historique prend plusieurs minutes sur le NAS). None quand tout est à jour."""
        current = effort_scans.get(user["id"])
        if current and current.running:
            return current.to_dict()
        todo = len(efforts.pending(conn, user["id"]))
        if not todo:
            return None
        progress = effort_scans[user["id"]] = efforts.ScanProgress(total=todo)

        def run():
            bg_conn = connect(settings.db_path)
            try:
                efforts.scan_pending(bg_conn, user["id"], progress)
                progress.finish()
            except Exception as e:
                progress.finish(f"Analyse interrompue : {e}")
            finally:
                bg_conn.close()

        background.add_task(run)
        return progress.to_dict()

    @api.get("/performance")
    def performance(user: User, conn: Conn, background: BackgroundTasks):
        if (scan := scanning(user, conn, background)) is not None:
            return {"status": "scanning", "scan": scan}
        last = effort_scans.get(user["id"])
        return {"status": "ready", "scan_error": last.error if last else None,
                **service.performance_summary(conn, user, date.today())}

    @api.post("/performance/rescan", status_code=202)
    def performance_rescan(user: User, conn: Conn, background: BackgroundTasks):
        """Recalcule les meilleurs efforts de toutes les courses (après un changement de calcul, ou pour vérifier)."""
        if (current := effort_scans.get(user["id"])) and current.running:
            raise HTTPException(409, "Analyse déjà en cours")
        efforts.reset(conn, user["id"])
        return {"status": "scanning", "scan": scanning(user, conn, background)}

    @api.get("/performance/goal")
    def performance_goal(user: User, conn: Conn, background: BackgroundTasks, race_date: date,
                         distance_km: float = Query(gt=0, le=100), target_time_s: int = Query(gt=0)):
        if scanning(user, conn, background) is not None:
            return {"verdict": None, "scanning": True}
        return service.check_goal(conn, user, distance_km, target_time_s, race_date, date.today())

    # --- Plans -----------------------------------------------------------------------------------------------

    @api.get("/plans")
    def list_plans(user: User, conn: Conn):
        return service.list_plans(conn, user["id"])

    @api.post("/plans", status_code=201)
    def create_plan(goal: GoalIn, user: User, conn: Conn):
        try:
            plan_id, plan = service.create_plan(conn, user["id"], Goal(**goal.model_dump()), date.today())
        except (ValueError, service.InvalidPlan) as e:
            raise HTTPException(422, str(e)) from e
        return {"id": plan_id, **plan}

    @api.post("/plans/multisport", status_code=201)
    def create_block(body: BlockIn, user: User, conn: Conn):
        try:
            plan_id, plan = service.create_block(conn, user, Preferences(**body.model_dump()), date.today())
        except (ValueError, service.InvalidPlan) as e:
            raise HTTPException(422, str(e)) from e
        return {"id": plan_id, **plan}

    @api.get("/plans/{plan_id}")
    def get_plan(plan_id: int, user: User, conn: Conn):
        plan = service.get_plan(conn, user["id"], plan_id)
        if plan is None:
            raise HTTPException(404)
        return {**plan, "narrating": plan_id in narrating, "narrate_error": narrate_errors.get(plan_id)}

    @api.delete("/plans/{plan_id}", status_code=204)
    def delete_plan(plan_id: int, user: User, conn: Conn):
        if plan_id in narrating:
            raise HTTPException(409, "Rédaction en cours : réessayez une fois terminée")
        if not service.delete_plan(conn, user["id"], plan_id):
            raise HTTPException(404)
        narrate_errors.pop(plan_id, None)

    @api.post("/plans/{plan_id}/narrate", status_code=202)
    def narrate(plan_id: int, user: User, conn: Conn, background: BackgroundTasks, weeks: list[int] | None = None):
        if service.get_plan(conn, user["id"], plan_id) is None:
            raise HTTPException(404)
        if plan_id in narrating:
            raise HTTPException(409, "Rédaction déjà en cours")
        narrating.add(plan_id)
        narrate_errors.pop(plan_id, None)

        # 1 à 2 min par semaine sur le NAS : on rend la main tout de suite.
        def run():
            bg_conn = connect(settings.db_path)
            try:
                service.narrate_plan(bg_conn, settings, user["id"], plan_id, weeks)
            except Exception as e:  # LLM injoignable, timeout... : remonté à l'interface
                narrate_errors[plan_id] = f"Rédaction impossible, le LLM local ne répond pas ({e})"
            finally:
                bg_conn.close()
                narrating.discard(plan_id)

        background.add_task(run)
        return {"status": "en cours"}

    app.include_router(api)

    # Interface web (build Vite) : fichiers statiques, et index.html pour toutes les routes du front.
    web_dir = settings.web_dir.resolve()
    if (web_dir / "index.html").exists():

        @app.get("/{path:path}", include_in_schema=False)
        def web(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (web_dir / path).resolve()
            if path and file.is_file() and file.is_relative_to(web_dir):
                return FileResponse(file)
            return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app(load_settings())
