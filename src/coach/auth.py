"""Comptes et sessions. Pas d'inscription libre : le premier compte (admin) est créé à l'installation,
les suivants par un admin."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta

# scrypt (stdlib) : n=2^14, r=8, p=1 → ~16 Mo de RAM et quelques dizaines de ms par vérification.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_DAYS = 30
MIN_PASSWORD_LENGTH = 10

# Anti force brute, en mémoire (un seul process) : 5 échecs → 15 min de blocage pour ce nom d'utilisateur.
MAX_FAILURES, LOCKOUT_S = 5, 15 * 60
_failures: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(candidate.hex(), digest)


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères")


class AccountError(Exception):
    pass


def validate_username(username: str) -> None:
    if not 2 <= len(username) <= 32 or not all(c.isalnum() or c in "._-" for c in username):
        raise ValueError("Nom d'utilisateur : 2 à 32 caractères (lettres, chiffres, . _ -)")


def create_user(
    conn, username: str, password: str, display_name: str | None = None, is_admin: bool = False, commit: bool = True
) -> int:
    validate_username(username)
    validate_password(password)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), display_name or username, int(is_admin)),
        )
    except sqlite3.IntegrityError as e:
        raise AccountError(f"Le nom d'utilisateur « {username} » est déjà pris") from e
    if commit:
        conn.commit()
    return cur.lastrowid


def needs_setup(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def create_first_admin(conn, username: str, password: str, display_name: str | None = None) -> int:
    """Crée le compte admin initial, uniquement si la base n'a encore aucun compte.

    BEGIN IMMEDIATE verrouille la base en écriture : deux premiers formulaires envoyés en même temps ne
    peuvent pas créer deux admins.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not needs_setup(conn):
            raise AccountError("L'application est déjà initialisée")
        user_id = create_user(conn, username, password, display_name, is_admin=True, commit=False)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return user_id


def list_users(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.is_admin, u.created_at,
               g.user_id IS NOT NULL AS garmin_connected, g.last_sync_at, g.last_error AS garmin_error,
               (SELECT COUNT(*) FROM activities a WHERE a.user_id = u.id) AS activities,
               (SELECT COUNT(*) FROM plans p WHERE p.user_id = u.id) AS plans
        FROM users u LEFT JOIN garmin_accounts g ON g.user_id = u.id
        ORDER BY u.username
        """
    )
    return [{**dict(r), "is_admin": bool(r["is_admin"]), "garmin_connected": bool(r["garmin_connected"])} for r in rows]


def delete_user(conn, user_id: int, acting_user_id: int) -> None:
    """Supprime un compte et toutes ses données (activités, plans, jetons Garmin, sessions)."""
    if user_id == acting_user_id:
        raise AccountError("Vous ne pouvez pas supprimer votre propre compte")
    row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise KeyError(user_id)
    if row["is_admin"] and conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] <= 1:
        raise AccountError("Il doit rester au moins un administrateur")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def set_password_for_id(conn, user_id: int, password: str) -> None:
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise KeyError(user_id)
    set_password(conn, row["username"], password)


def set_password(conn, username: str, password: str) -> None:
    validate_password(password)
    cur = conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username))
    if cur.rowcount == 0:
        raise KeyError(username)
    # Changer de mot de passe déconnecte toutes les sessions existantes.
    conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))
    conn.commit()


def _locked(username: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(username.lower(), []) if now - t < LOCKOUT_S]
    _failures[username.lower()] = recent
    return len(recent) >= MAX_FAILURES


def authenticate(conn, username: str, password: str) -> dict | None:
    if _locked(username):
        return None
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        _failures.setdefault(username.lower(), []).append(time.monotonic())
        return None
    _failures.pop(username.lower(), None)
    return dict(row)


def user_for_id(conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_for_api_key(conn, api_key: str) -> dict | None:
    if not api_key:
        return None
    row = conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
    return dict(row) if row else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(UTC).isoformat(),))
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), user_id, expires.isoformat()),
    )
    conn.commit()
    return token


def user_for_session(conn, token: str) -> dict | None:
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(token), datetime.now(UTC).isoformat()),
    ).fetchone()
    return dict(row) if row else None


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    conn.commit()
