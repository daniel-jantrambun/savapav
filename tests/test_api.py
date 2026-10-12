import dataclasses
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from coach import auth
from coach.api import create_app
from coach.config import load_settings
from coach.db import connect
from coach.ingest import garmin
from coach.ingest.fit_files import iter_fit_payloads

PASSWORD = "correct horse battery"
GOAL = {"distance_km": 10, "target_time_s": 2700, "race_date": "2027-03-07", "runs_per_week": 3}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(load_settings(), data_dir=tmp_path, db_path=tmp_path / "coach.sqlite",
                               fit_dir=tmp_path / "fit", secret_key="phrase secrète de test")


@pytest.fixture
def conn(settings):
    c = connect(settings.db_path)
    auth.create_user(c, "alice", PASSWORD)
    auth.create_user(c, "bob", PASSWORD)
    auth._failures.clear()
    yield c
    c.close()


def client_for(settings, username):
    client = TestClient(create_app(settings))
    assert client.post("/api/login", json={"username": username, "password": PASSWORD}).status_code == 200
    return client


def test_routes_require_login(settings, conn):
    client = TestClient(create_app(settings))
    for path in ("/api/me", "/api/activities", "/api/plans", "/api/load", "/api/me/garmin"):
        assert client.get(path).status_code == 401


def test_plans_are_isolated_between_users(settings, conn):
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")
    plan_id = alice.post("/api/plans", json=GOAL).json()["id"]
    assert alice.get(f"/api/plans/{plan_id}").status_code == 200
    assert bob.get(f"/api/plans/{plan_id}").status_code == 404
    assert bob.post(f"/api/plans/{plan_id}/narrate").status_code == 404
    assert bob.get("/api/plans").json() == []


def test_wrong_password_then_lockout(settings, conn):
    client = TestClient(create_app(settings))
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/api/login", json={"username": "alice", "password": "nope"}).status_code == 401
    # Même le bon mot de passe est refusé pendant le blocage.
    assert client.post("/api/login", json={"username": "alice", "password": PASSWORD}).status_code == 401


def test_logout_and_password_change_end_sessions(settings, conn):
    client = client_for(settings, "alice")
    assert client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": "un autre secret"}
                       ).status_code == 204
    assert client.get("/api/me").status_code == 401
    client = TestClient(create_app(settings))
    client.post("/api/login", json={"username": "alice", "password": "un autre secret"})
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_profile_update(settings, conn):
    client = client_for(settings, "alice")
    assert client.patch("/api/me", json={"hr_rest": 48, "hr_max": 190}).json()["hr_max"] == 190
    assert client.patch("/api/me", json={"hr_max": 400}).status_code == 422


def test_garmin_tokens_are_encrypted_and_never_the_password(settings, conn):
    class FakeClient:
        class client:
            @staticmethod
            def dumps():
                return '{"di_token": "abc", "di_refresh_token": "def"}'

    user_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    garmin._save_tokens(conn, user_id, FakeClient, settings.secret_key)
    stored = conn.execute("SELECT tokens_encrypted FROM garmin_accounts WHERE user_id = ?", (user_id,)).fetchone()[0]
    assert b"di_token" not in stored
    assert garmin._fernet(settings.secret_key).decrypt(stored).decode().startswith('{"di_token"')
    assert garmin.status(conn, user_id)["connected"]


def test_garmin_requires_secret_key(settings, conn):
    client = client_for(dataclasses.replace(settings, secret_key=None), "alice")
    response = client.post("/api/me/garmin", json={"email": "a@b.c", "password": "x"})
    assert response.status_code == 400 and "COACH_SECRET_KEY" in response.json()["detail"]


def test_nested_zip_from_garmin_export(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("123_ACTIVITY.fit", b"fit-data")
    with zipfile.ZipFile(tmp_path / "export.zip", "w") as z:
        z.writestr("DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip", inner.getvalue())
        z.writestr("DI_CONNECT/other.json", b"{}")
    assert [payload for _, payload in iter_fit_payloads(tmp_path)] == [b"fit-data"]


def test_file_import_runs_in_background(settings, conn):
    alice = client_for(settings, "alice")
    assert alice.get("/api/activities/import").json() == {"running": False}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("1_ACTIVITY.fit", b"pas un vrai FIT")
        z.writestr("2_ACTIVITY.fit", b"pas un vrai FIT")
    response = alice.post("/api/activities/import", files={"file": ("export.zip", archive.getvalue())})
    assert response.status_code == 202 and response.json()["running"]
    # Le TestClient exécute la tâche de fond avant de rendre la main : l'import est terminé.
    status = alice.get("/api/activities/import").json()
    assert status["running"] is False and status["error"] is None
    assert (status["files"], status["imported"], status["skipped"]) == (2, 0, 2)
    assert client_for(settings, "bob").get("/api/activities/import").json() == {"running": False}
    bad = alice.post("/api/activities/import", files={"file": ("notes.txt", b"x")})
    assert bad.status_code == 422


def test_delete_activities_only_touches_own_rows(settings, conn):
    from coach.db import upsert_activity

    for name in ("alice", "bob"):
        user_id = auth.authenticate(conn, name, PASSWORD)["id"]
        upsert_activity(conn, {"user_id": user_id, "id": f"fit:{name}", "source": "fit", "sport": "running",
                               "start_time": "2026-10-01T07:00:00+00:00", "duration_s": 1800, "distance_m": 5000})
    conn.commit()
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")
    assert alice.delete("/api/activities").json() == {"deleted": 1}
    assert alice.get("/api/activities").json() == []
    assert [a["id"] for a in bob.get("/api/activities").json()] == ["fit:bob"]
    assert alice.delete("/api/activities").json() == {"deleted": 0}


def test_stats_all_history_starts_at_first_activity(settings, conn):
    from datetime import date

    from coach import service
    from coach.db import upsert_activity

    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    alice = client_for(settings, "alice")
    today = date(2026, 10, 12)  # lundi
    assert service.stats(conn, {"id": alice_id}, today, 0) == {"weeks": 4, "categories": {}}  # aucune activité
    for id_, sport, day in (("old", "running", "2014-10-01"), ("bike", "cycling", "2026-09-30")):
        upsert_activity(conn, {"user_id": alice_id, "id": f"fit:{id_}", "source": "fit", "sport": sport,
                               "start_time": f"{day}T07:00:00+00:00", "duration_s": 1800, "distance_m": 5000})
    conn.commit()

    stats = service.stats(conn, {"id": alice_id}, today, 0)
    run, bike = stats["categories"]["run"], stats["categories"]["bike"]
    assert service.stats_start(today, stats["weeks"]) == date(2014, 9, 1)  # 1er du mois de la 1re activité
    assert run["bucket"] == "month" and run["weeks"][0]["start"] == "2014-09-01"
    assert run["weeks"][1]["start"] == "2014-10-01" and run["weeks"][1]["sessions"] == 1
    # Le vélo démarre à sa propre première activité, par semaine : 3 semaines et non 12 ans de mois vides.
    assert bike["bucket"] == "week"
    assert [w["start"] for w in bike["weeks"]] == ["2026-09-28", "2026-10-05", "2026-10-12"]
    # Sur une période plus courte que l'historique, les semaines vides restent (la course ne commence pas là).
    year = service.stats(conn, {"id": alice_id}, today, 52)["categories"]
    assert set(year) == {"bike"} and len(year["bike"]["weeks"]) == 3

    assert alice.get("/api/stats?weeks=0").json()["categories"]["run"]["total"]["sessions"] == 1
    assert alice.get("/api/stats?weeks=260").json()["categories"]["bike"]["total"]["sessions"] == 1
    assert [a["id"] for a in alice.get("/api/activities/category?category=run&weeks=0").json()] == ["fit:old"]


def test_hidden_activities_are_filtered_unless_requested(settings, conn):
    from coach.db import upsert_activity

    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    for id_, day in (("old", "2026-09-01"), ("new", "2026-10-01")):
        upsert_activity(conn, {"user_id": alice_id, "id": f"fit:{id_}", "source": "fit", "sport": "running",
                               "start_time": f"{day}T07:00:00+00:00", "duration_s": 1800, "distance_m": 5000})
    conn.commit()
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")

    assert bob.patch("/api/activities/fit:old", json={"hidden": True}).status_code == 404  # l'activité d'un autre
    assert alice.patch("/api/activities/fit:old", json={"hidden": True}).json() == {"hidden": True}
    assert alice.get("/api/activities/fit:old").json()["hidden"] is True

    assert [a["id"] for a in alice.get("/api/activities").json()] == ["fit:new"]
    assert [a["id"] for a in alice.get("/api/activities?hidden=true").json()] == ["fit:new", "fit:old"]
    assert [a["id"] for a in alice.get("/api/activities/category?category=run&weeks=0").json()] == ["fit:new"]
    assert alice.get("/api/stats?weeks=0").json()["categories"]["run"]["total"]["sessions"] == 1
    assert alice.get("/api/stats?weeks=0&hidden=true").json()["categories"]["run"]["total"]["sessions"] == 2

    # Un réimport du même fichier ne fait pas réapparaître l'activité.
    upsert_activity(conn, {"user_id": alice_id, "id": "fit:old", "source": "fit", "sport": "running",
                           "start_time": "2026-09-01T07:00:00+00:00", "duration_s": 1800, "distance_m": 5000})
    conn.commit()
    assert alice.get("/api/activities/fit:old").json()["hidden"] is True
    assert alice.patch("/api/activities/fit:old", json={"hidden": False}).json() == {"hidden": False}
    assert len(alice.get("/api/activities").json()) == 2


def test_multisport_sessions_are_grouped_by_file(settings, conn):
    from coach.db import upsert_activity

    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    legs = [("fit:tri", "swimming", "07:00"), ("fit:tri:2", "cycling", "07:30"), ("fit:tri:4", "running", "09:00")]
    for id_, sport, time in legs:
        upsert_activity(conn, {"user_id": alice_id, "id": id_, "source": "fit", "sport": sport,
                               "start_time": f"2026-10-04T{time}:00+00:00", "duration_s": 1500,
                               "distance_m": 1000, "file_path": "/fit/tri.fit"})
    upsert_activity(conn, {"user_id": alice_id, "id": "fit:solo", "source": "fit", "sport": "running",
                           "start_time": "2026-10-05T07:00:00+00:00", "duration_s": 1800, "distance_m": 5000,
                           "file_path": "/fit/solo.fit"})
    conn.commit()
    alice = client_for(settings, "alice")

    by_id = {a["id"]: a["multisport"] for a in alice.get("/api/activities").json()}
    assert by_id["fit:solo"] is None
    assert by_id["fit:tri:2"]["kind"] == "triathlon"
    assert (by_id["fit:tri:2"]["position"], by_id["fit:tri:2"]["count"]) == (2, 3)
    assert [p["category"] for p in by_id["fit:tri"]["parts"]] == ["swim", "bike", "run"]
    assert alice.get("/api/activities/fit:tri:4").json()["multisport"]["position"] == 3
    assert alice.get("/api/activities/category?category=bike&weeks=0").json()[0]["multisport"]["kind"] == "triathlon"


def test_serves_web_app_with_spa_fallback(settings, conn, tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<div id=root></div>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    client = TestClient(create_app(dataclasses.replace(settings, web_dir=web)))
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/plans/3").text == "<div id=root></div>"  # route du front
    assert client.get("/api/inconnu").status_code == 404
    assert "nope" not in client.get("/../secret.txt").text


def test_first_account_becomes_admin_only_once(settings):
    client = TestClient(create_app(settings))
    assert client.get("/api/setup").json() == {"needs_setup": True}
    response = client.post("/api/setup", json={"username": "papa", "password": PASSWORD, "display_name": "Papa"})
    assert response.status_code == 201 and response.json()["is_admin"] is True
    assert client.get("/api/me").status_code == 200  # connecté directement
    assert client.get("/api/setup").json() == {"needs_setup": False}
    other = TestClient(create_app(settings))
    assert other.post("/api/setup", json={"username": "intrus", "password": PASSWORD}).status_code == 409


def test_setup_rejects_weak_password_and_bad_username(settings):
    client = TestClient(create_app(settings))
    assert client.post("/api/setup", json={"username": "papa", "password": "court"}).status_code == 422
    assert client.post("/api/setup", json={"username": "a b", "password": PASSWORD}).status_code == 422
    assert client.get("/api/setup").json() == {"needs_setup": True}


def test_admin_manages_accounts(settings, conn):
    auth.create_user(conn, "root", PASSWORD, is_admin=True)
    admin, alice = client_for(settings, "root"), client_for(settings, "alice")

    assert alice.get("/api/admin/users").status_code == 403
    assert alice.post("/api/admin/users", json={"username": "x", "password": PASSWORD}).status_code == 403

    created = admin.post("/api/admin/users", json={"username": "zoe", "password": PASSWORD, "display_name": "Zoé"})
    assert created.status_code == 201
    assert admin.post("/api/admin/users", json={"username": "ZOE", "password": PASSWORD}).status_code == 409
    assert {u["username"] for u in admin.get("/api/admin/users").json()} == {"alice", "bob", "root", "zoe"}

    # Réinitialisation : l'ancienne session d'alice est fermée, le nouveau mot de passe fonctionne.
    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    assert admin.post(f"/api/admin/users/{alice_id}/password", json={"new_password": "nouveau secret"}
                      ).status_code == 204
    assert alice.get("/api/me").status_code == 401
    assert auth.authenticate(conn, "alice", "nouveau secret") is not None


def test_delete_account_removes_its_data(settings, conn):
    auth.create_user(conn, "root", PASSWORD, is_admin=True)
    admin, bob = client_for(settings, "root"), client_for(settings, "bob")
    bob.post("/api/plans", json=GOAL)
    bob_id = bob.get("/api/me").json()["id"]
    (settings.fit_dir / str(bob_id)).mkdir(parents=True)

    assert admin.delete(f"/api/admin/users/{bob_id}").status_code == 204
    assert bob.get("/api/me").status_code == 401
    assert conn.execute("SELECT COUNT(*) FROM plans WHERE user_id = ?", (bob_id,)).fetchone()[0] == 0
    assert not (settings.fit_dir / str(bob_id)).exists()
    assert admin.delete(f"/api/admin/users/{bob_id}").status_code == 404


def test_admin_cannot_delete_self_or_last_admin(settings, conn):
    root_id = auth.create_user(conn, "root", PASSWORD, is_admin=True)
    admin = client_for(settings, "root")
    assert admin.delete(f"/api/admin/users/{root_id}").status_code == 409
    other_id = admin.post("/api/admin/users", json={"username": "maman", "password": PASSWORD, "is_admin": True}
                          ).json()["id"]
    # Deux admins : l'un peut supprimer l'autre, mais le dernier restant est protégé.
    assert admin.delete(f"/api/admin/users/{other_id}").status_code == 204
    with pytest.raises(auth.AccountError):
        auth.delete_user(conn, root_id, acting_user_id=-1)


def test_migration_makes_oldest_account_admin(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.sqlite"
    old = sqlite3.connect(db_path)
    old.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,"
        " password_hash TEXT NOT NULL, display_name TEXT NOT NULL, hr_rest INTEGER NOT NULL DEFAULT 50,"
        " hr_max INTEGER NOT NULL DEFAULT 185, created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO users (username, password_hash, display_name) VALUES ('premier', 'x', 'P'), ('second', 'x', 'S');"
    )
    old.commit()
    old.close()
    conn = connect(db_path)
    assert [tuple(r) for r in conn.execute("SELECT username, is_admin FROM users ORDER BY id")] == [
        ("premier", 1), ("second", 0)]


def test_garmin_errors_include_library_warnings():
    import logging

    with garmin._diagnostics() as warnings:
        logging.getLogger("garminconnect.client").warning("DI token exchange failed (%s)", "HTTP 403")
        logging.getLogger("garminconnect.client").warning("DI token exchange failed (%s)", "HTTP 403")
    error = garmin._error("Connexion Garmin refusée", Exception("JWT_WEB cookie not set"), warnings)
    assert str(error) == ("Connexion Garmin refusée : JWT_WEB cookie not set "
                          "(détails : DI token exchange failed (HTTP 403))")


BLOCK = {"focus": {"run": "vitesse", "bike": "endurance", "gym": "force"}, "sessions_per_week": 6}


def test_analysis_and_multisport_block(settings, conn):
    from datetime import date, timedelta

    from coach.db import upsert_activity

    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    for k in range(1, 20, 2):
        day = date.today() - timedelta(days=k)
        upsert_activity(conn, {"user_id": alice_id, "id": f"fit:{k}", "source": "fit", "sport": "running",
                               "start_time": f"{day}T07:00:00+00:00", "duration_s": 3000, "distance_m": 9000})
    upsert_activity(conn, {"user_id": alice_id, "id": "fit:gym", "source": "fit", "sport": "training",
                           "sub_sport": "strength_training", "start_time": f"{date.today() - timedelta(days=2)}"
                           "T18:00:00+00:00", "duration_s": 2700})
    conn.commit()
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")

    analysis = alice.get("/api/analysis").json()
    assert set(analysis["sports"]) == {"run", "gym"}
    assert bob.get("/api/analysis").json()["sports"] == {}

    created = alice.post("/api/plans/multisport", json=BLOCK)
    assert created.status_code == 201
    plan = alice.get(f"/api/plans/{created.json()['id']}").json()
    assert plan["mode"] == "multisport" and plan["number"] == 1 and len(plan["weeks"]) == 4
    assert plan["goal"]["focus"] == BLOCK["focus"]
    # Le bloc suivant est numéroté à la suite ; les plans course restent listés avec leur mode.
    assert alice.post("/api/plans/multisport", json=BLOCK).json()["number"] == 2
    alice.post("/api/plans", json=GOAL)
    assert [p["goal"]["mode"] for p in alice.get("/api/plans").json()] == ["race", "multisport", "multisport"]
    assert bob.get(f"/api/plans/{created.json()['id']}").status_code == 404


def test_multisport_rejects_bad_preferences(settings, conn):
    alice = client_for(settings, "alice")
    assert alice.post("/api/plans/multisport", json={"focus": {"gym": "vitesse"}}).status_code == 422
    assert alice.post("/api/plans/multisport", json={"focus": {}}).status_code == 422
    response = alice.post("/api/plans/multisport", json={"focus": {"run": "vitesse", "swim": "technique"},
                                                         "sessions_per_week": 1})
    assert response.status_code == 422 and "au moins 2 séances" in response.json()["detail"]


def test_multisport_week_prompt_keeps_numbers():
    import json as jsonlib

    from coach.llm import multisport_week_prompt

    week = {"index": 0, "deload": False, "minutes": 100, "sessions": [
        {"date": "2026-10-13", "sport": "run", "kind": "intervals", "duration_min": 55, "distance_km": 10.2,
         "pace_s_per_km": 270, "pace_s_per_100m": None, "hr_low": None, "hr_high": None, "reps": 5, "rep_m": 1000,
         "rep_s": None, "recovery_s": 90, "focus": None},
        {"date": "2026-10-14", "sport": "gym", "kind": "strength", "duration_min": 45, "distance_km": None,
         "pace_s_per_km": None, "pace_s_per_100m": None, "hr_low": None, "hr_high": None, "reps": None,
         "rep_m": None, "rep_s": None, "recovery_s": None, "focus": "lower"},
    ]}
    sessions = jsonlib.loads(multisport_week_prompt(week))["seances"]
    assert sessions[0]["allure"] == "4:30/km" and sessions[0]["repetitions"] == "5x1000m, récup 90s"
    assert sessions[1] == {"date": "2026-10-14", "sport": "salle", "type": "renforcement", "duree_min": 45,
                           "zone": "bas du corps"}


def test_delete_plan(settings, conn):
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")
    first = alice.post("/api/plans/multisport", json={"focus": {"run": "maintien"}, "sessions_per_week": 3}).json()
    second = alice.post("/api/plans/multisport", json={"focus": {"run": "maintien"}, "sessions_per_week": 3}).json()
    conn.execute("INSERT INTO plan_texts (plan_id, week, text, model) VALUES (?, 0, 'texte', 'm')", (first["id"],))
    conn.commit()

    assert bob.delete(f"/api/plans/{first['id']}").status_code == 404  # le plan d'un autre
    assert alice.delete(f"/api/plans/{first['id']}").status_code == 204
    assert alice.get(f"/api/plans/{first['id']}").status_code == 404
    assert alice.delete(f"/api/plans/{first['id']}").status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM plan_texts WHERE plan_id = ?", (first["id"],)).fetchone()[0] == 0
    # La numérotation continue après le dernier bloc restant.
    assert second["number"] == 2
    assert alice.post("/api/plans/multisport", json={"focus": {"run": "maintien"}, "sessions_per_week": 3}
                      ).json()["number"] == 3


def test_activity_detail_and_stats_are_per_user(settings, conn):
    from coach.db import upsert_activity

    alice_id = auth.authenticate(conn, "alice", PASSWORD)["id"]
    upsert_activity(conn, {"user_id": alice_id, "id": "fit:abc", "source": "fit", "sport": "cycling",
                           "start_time": "2026-10-01T07:00:00+00:00", "duration_s": 3600, "distance_m": 30000,
                           "file_path": "/nulle/part.fit"})
    conn.commit()
    alice, bob = client_for(settings, "alice"), client_for(settings, "bob")

    detail = alice.get("/api/activities/fit:abc").json()
    assert detail["category"] == "bike" and detail["streams"] is None and "file_path" not in detail
    assert bob.get("/api/activities/fit:abc").status_code == 404
    assert [a["id"] for a in alice.get("/api/activities?category=bike").json()] == ["fit:abc"]
    assert alice.get("/api/activities?category=run").json() == []
    assert [a["id"] for a in alice.get("/api/activities?limit=1&offset=0").json()] == ["fit:abc"]
    assert alice.get("/api/activities?limit=1&offset=1").json() == []
    assert alice.get("/api/activities?offset=-1").status_code == 422
    assert alice.get("/api/stats?weeks=104").json()["categories"]["bike"]["total"]["km"] == 30.0
    from datetime import date

    from coach import service
    assert [a["id"] for a in service.activities(conn, alice_id, since=date(2026, 10, 1))] == ["fit:abc"]
    assert service.activities(conn, alice_id, since=date(2026, 10, 2)) == []
    stats = service.stats(conn, {"id": alice_id}, date(2026, 10, 7), weeks=1)  # semaine du lundi 5 octobre
    assert stats["categories"] == {}
    assert bob.get("/api/stats").json()["categories"] == {}
    assert alice.get("/api/stats?weeks=1").status_code == 422
