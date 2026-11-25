#!/usr/bin/env python3
"""Suivi des sinistres en copropriété.

Serveur web sans dépendance externe (bibliothèque standard Python + SQLite).
Lancement : python3 app.py  puis ouvrir http://localhost:8000
"""
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.environ.get("SINISTRES_DB", os.path.join(BASE_DIR, "sinistres.db"))
DOCS_DIR = os.environ.get("SINISTRES_DOCS", os.path.join(BASE_DIR, "documents"))
MAX_UPLOAD = int(os.environ.get("SINISTRES_MAX_UPLOAD_MO", 25)) * 1024 * 1024
SESSION_HOURS = int(os.environ.get("SINISTRES_SESSION_HEURES", 12))
# À activer quand l'application est servie en HTTPS (derrière Caddy, Nginx…)
COOKIE_SECURE = os.environ.get("SINISTRES_COOKIE_SECURE") == "1"
# À activer derrière un proxy inverse pour lire l'adresse IP réelle du client
TRUST_PROXY = os.environ.get("SINISTRES_TRUST_PROXY") == "1"
ROLES = ["admin", "gestionnaire"]

TYPES = [
    "Dégât des eaux",
    "Incendie",
    "Bris de glace",
    "Vol / Vandalisme",
    "Tempête / Grêle / Neige",
    "Catastrophe naturelle",
    "Responsabilité civile",
    "Dommages électriques",
    "Choc de véhicule",
    "Autre",
]

STATUTS = [
    "À déclarer",
    "Déclaré",
    "En attente d'expertise",
    "Expertise réalisée",
    "Devis / Travaux",
    "Indemnisation en cours",
    "Clôturé",
    "Refusé / Sans suite",
]
STATUTS_CLOS = {"Clôturé", "Refusé / Sans suite"}

JOURNAL_TYPES = ["Note", "Appel", "Courriel", "Courrier", "Relance", "Expertise",
                 "Devis", "Travaux", "Règlement", "Document", "Changement de statut"]

DOC_CATEGORIES = ["Photo", "Constat amiable", "Déclaration", "Rapport d'expertise",
                  "Devis", "Facture", "Courrier / Courriel", "Quittance / Règlement", "Autre"]

# Types de fichiers affichables directement dans le navigateur ; les autres sont
# toujours servis en téléchargement (évite l'exécution de contenu HTML/SVG envoyé).
INLINE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}

# --------------------------------------------------------------------------- #
# Schéma et migrations (PRAGMA user_version)
# --------------------------------------------------------------------------- #
MIGRATIONS = [
    # v1 — schéma initial
    """
    CREATE TABLE IF NOT EXISTS coproprietes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        adresse TEXT DEFAULT '',
        assureur TEXT DEFAULT '',
        numero_police TEXT DEFAULT '',
        courtier TEXT DEFAULT '',
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sinistres (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT UNIQUE NOT NULL,
        copropriete_id INTEGER REFERENCES coproprietes(id) ON DELETE SET NULL,
        type TEXT NOT NULL,
        statut TEXT NOT NULL,
        date_survenance TEXT DEFAULT '',
        date_constat TEXT DEFAULT '',
        date_declaration TEXT DEFAULT '',
        localisation TEXT DEFAULT '',
        parties TEXT DEFAULT 'Communes',
        declarant TEXT DEFAULT '',
        declarant_contact TEXT DEFAULT '',
        tiers TEXT DEFAULT '',
        description TEXT DEFAULT '',
        assureur TEXT DEFAULT '',
        numero_sinistre_assureur TEXT DEFAULT '',
        convention TEXT DEFAULT '',
        expert TEXT DEFAULT '',
        date_expertise TEXT DEFAULT '',
        entreprise TEXT DEFAULT '',
        montant_estime REAL,
        montant_devis REAL,
        franchise REAL,
        montant_indemnise REAL,
        date_indemnisation TEXT DEFAULT '',
        prochaine_action TEXT DEFAULT '',
        date_echeance TEXT DEFAULT '',
        date_cloture TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sinistre_id INTEGER NOT NULL REFERENCES sinistres(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        type TEXT NOT NULL,
        commentaire TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_journal_sinistre ON journal(sinistre_id);
    """,
    # v2 — pièces jointes
    """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sinistre_id INTEGER NOT NULL REFERENCES sinistres(id) ON DELETE CASCADE,
        nom TEXT NOT NULL,
        categorie TEXT NOT NULL,
        mime TEXT NOT NULL,
        taille INTEGER NOT NULL,
        fichier TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_documents_sinistre ON documents(sinistre_id);
    """,
    # v3 — comptes utilisateurs et sessions
    """
    CREATE TABLE utilisateurs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE,
        nom TEXT NOT NULL,
        email TEXT DEFAULT '',
        role TEXT NOT NULL DEFAULT 'gestionnaire',
        mot_de_passe TEXT NOT NULL,
        actif INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        derniere_connexion TEXT DEFAULT ''
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        utilisateur_id INTEGER NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    ALTER TABLE journal ADD COLUMN auteur_id INTEGER REFERENCES utilisateurs(id);
    ALTER TABLE documents ADD COLUMN auteur_id INTEGER REFERENCES utilisateurs(id);
    """,
    # v4 — paramètres du cabinet (courriers) et coordonnées de l'assureur
    """
    CREATE TABLE parametres (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL DEFAULT '');
    ALTER TABLE coproprietes ADD COLUMN assureur_adresse TEXT DEFAULT '';
    ALTER TABLE coproprietes ADD COLUMN assureur_email TEXT DEFAULT '';
    """,
]

COPRO_FIELDS = ["nom", "adresse", "assureur", "assureur_adresse", "assureur_email",
                "numero_police", "courtier", "notes"]
PARAMETRES = ["cabinet_nom", "cabinet_adresse", "cabinet_telephone", "cabinet_email",
              "cabinet_ville", "cabinet_mentions"]
SINISTRE_FIELDS = [
    "copropriete_id", "type", "statut", "date_survenance", "date_constat",
    "date_declaration", "localisation", "parties", "declarant", "declarant_contact",
    "tiers", "description", "assureur", "numero_sinistre_assureur", "convention",
    "expert", "date_expertise", "entreprise", "montant_estime", "montant_devis",
    "franchise", "montant_indemnise", "date_indemnisation", "prochaine_action",
    "date_echeance", "date_cloture",
]
MONEY_FIELDS = {"montant_estime", "montant_devis", "franchise", "montant_indemnise"}
DATE_FIELDS = {f for f in SINISTRE_FIELDS if f.startswith("date_")}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class NotFound(ApiError):
    def __init__(self, message="Introuvable"):
        super().__init__(message, HTTPStatus.NOT_FOUND)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=None):
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # lectures et écritures simultanées
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i}")
            conn.commit()
    finally:
        conn.close()
    os.makedirs(DOCS_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def parse_money(v, label):
    if v in (None, ""):
        return None
    try:
        return round(float(str(v).replace(",", ".").replace(" ", "").replace(" ", "")), 2)
    except ValueError:
        raise ApiError(f"Montant invalide pour « {label} ».")


def check_date(v, label):
    v = str(v or "").strip()
    if v and not DATE_RE.match(v):
        raise ApiError(f"Date invalide pour « {label} » (format AAAA-MM-JJ).")
    return v


def clean_copro(data):
    out = {f: str(data.get(f) or "").strip() for f in COPRO_FIELDS}
    if not out["nom"]:
        raise ApiError("Le nom de la copropriété est obligatoire.")
    return out


def clean_sinistre(data):
    out = {}
    for f in SINISTRE_FIELDS:
        v = data.get(f)
        if f in MONEY_FIELDS:
            out[f] = parse_money(v, f)
        elif f == "copropriete_id":
            out[f] = int(v) if v not in (None, "") else None
        elif f in DATE_FIELDS:
            out[f] = check_date(v, f)
        else:
            out[f] = str(v or "").strip()
    if out["type"] not in TYPES:
        raise ApiError("Type de sinistre invalide.")
    if out["statut"] not in STATUTS:
        raise ApiError("Statut invalide.")
    if out["statut"] in STATUTS_CLOS and not out["date_cloture"]:
        out["date_cloture"] = date.today().isoformat()
    if out["statut"] not in STATUTS_CLOS:
        out["date_cloture"] = ""
    return out


# --------------------------------------------------------------------------- #
# Comptes et sessions
# --------------------------------------------------------------------------- #
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def verify_password(password, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32,
                              n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_password_strength(pw):
    if len(pw or "") < 8:
        raise ApiError("Le mot de passe doit contenir au moins 8 caractères.")


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, uid):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    conn.execute("INSERT INTO sessions (token_hash, utilisateur_id, expires_at, created_at) "
                 "VALUES (?,?,?,?)", (token_hash(token), uid, expires, now_iso()))
    conn.execute("UPDATE utilisateurs SET derniere_connexion = ? WHERE id = ?", (now_iso(), uid))
    return token


def session_user(conn, token):
    if not token:
        return None
    row = conn.execute("""
        SELECT u.id, u.identifiant, u.nom, u.email, u.role, s.expires_at
        FROM sessions s JOIN utilisateurs u ON u.id = s.utilisateur_id
        WHERE s.token_hash = ? AND u.actif = 1""", (token_hash(token),)).fetchone()
    if not row or row["expires_at"] < now_iso():
        return None
    # Expiration glissante : la session est prolongée à chaque utilisation
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                 (expires, token_hash(token)))
    u = dict(row)
    del u["expires_at"]
    return u


def session_cookie(token, max_age):
    parts = [f"sid={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)


class LoginThrottle:
    """Limite les tentatives de connexion échouées (8 par quart d'heure)."""

    def __init__(self, limit=8, window=900):
        self.limit, self.window, self.fails, self.lock = limit, window, {}, threading.Lock()

    def _recent(self, key):
        now = time.monotonic()
        self.fails[key] = [t for t in self.fails.get(key, []) if now - t < self.window]
        return self.fails[key]

    def blocked(self, key):
        with self.lock:
            return len(self._recent(key)) >= self.limit

    def fail(self, key):
        with self.lock:
            self._recent(key).append(time.monotonic())

    def reset(self, key):
        with self.lock:
            self.fails.pop(key, None)


THROTTLE = LoginThrottle()

USER_PUBLIC = "id, identifiant, nom, email, role, actif, created_at, derniere_connexion"


def clean_user(data, creating):
    out = {
        "nom": str(data.get("nom") or "").strip(),
        "email": str(data.get("email") or "").strip(),
        "role": data.get("role") or "gestionnaire",
    }
    if not out["nom"]:
        raise ApiError("Le nom est obligatoire.")
    if out["role"] not in ROLES:
        raise ApiError("Rôle invalide.")
    if creating:
        ident = str(data.get("identifiant") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9._@-]{3,60}", ident):
            raise ApiError("Identifiant invalide (3 à 60 caractères : lettres, chiffres, . _ - @).")
        out["identifiant"] = ident
    return out


def count_admins(conn, exclude=None):
    return conn.execute("SELECT COUNT(*) FROM utilisateurs WHERE role = 'admin' AND actif = 1 "
                        "AND id != ?", (exclude or 0,)).fetchone()[0]


# --------------------------------------------------------------------------- #
# Accès aux données
# --------------------------------------------------------------------------- #
def next_reference(conn):
    prefix = f"SIN-{date.today().year}-"
    row = conn.execute(
        "SELECT reference FROM sinistres WHERE reference LIKE ? ORDER BY reference DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = int(row["reference"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{n:04d}"


def list_coproprietes(conn):
    rows = conn.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM sinistres s WHERE s.copropriete_id = c.id) AS nb_sinistres,
               (SELECT COUNT(*) FROM sinistres s WHERE s.copropriete_id = c.id
                  AND s.statut NOT IN ('Clôturé', 'Refusé / Sans suite')) AS nb_ouverts
        FROM coproprietes c ORDER BY c.nom COLLATE NOCASE
    """).fetchall()
    return [dict(r) for r in rows]


SINISTRE_SELECT = """
    SELECT s.*, c.nom AS copropriete_nom,
           (SELECT MAX(j.date) FROM journal j WHERE j.sinistre_id = s.id) AS derniere_action,
           (SELECT COUNT(*) FROM documents d WHERE d.sinistre_id = s.id) AS nb_documents
    FROM sinistres s LEFT JOIN coproprietes c ON c.id = s.copropriete_id
"""


def list_sinistres(conn):
    return [dict(r) for r in conn.execute(SINISTRE_SELECT + " ORDER BY s.created_at DESC")]


def get_sinistre(conn, sid):
    row = conn.execute(SINISTRE_SELECT + " WHERE s.id = ?", (sid,)).fetchone()
    if not row:
        raise NotFound("Sinistre introuvable.")
    s = dict(row)
    s["journal"] = [dict(r) for r in conn.execute(
        "SELECT j.*, u.nom AS auteur FROM journal j LEFT JOIN utilisateurs u ON u.id = j.auteur_id "
        "WHERE j.sinistre_id = ? ORDER BY j.date DESC, j.id DESC", (sid,))]
    s["documents"] = [dict(r) for r in conn.execute(
        "SELECT d.id, d.nom, d.categorie, d.mime, d.taille, d.created_at, u.nom AS auteur "
        "FROM documents d LEFT JOIN utilisateurs u ON u.id = d.auteur_id "
        "WHERE d.sinistre_id = ? ORDER BY d.created_at DESC, d.id DESC", (sid,))]
    return s


def require_sinistre(conn, sid):
    if not conn.execute("SELECT 1 FROM sinistres WHERE id = ?", (sid,)).fetchone():
        raise NotFound("Sinistre introuvable.")


def add_journal(conn, sid, jtype, commentaire, jdate=None, auteur_id=None):
    conn.execute(
        "INSERT INTO journal (sinistre_id, date, type, commentaire, created_at, auteur_id) "
        "VALUES (?,?,?,?,?,?)",
        (sid, jdate or date.today().isoformat(), jtype, commentaire, now_iso(), auteur_id),
    )


def create_sinistre(conn, data, user=None):
    s = clean_sinistre(data)
    s["reference"] = next_reference(conn)
    s["created_at"] = s["updated_at"] = now_iso()
    cols = list(s)
    cur = conn.execute(
        f"INSERT INTO sinistres ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [s[c] for c in cols],
    )
    sid = cur.lastrowid
    add_journal(conn, sid, "Changement de statut", f"Dossier créé — statut : {s['statut']}",
                auteur_id=user and user["id"])
    return sid


def update_sinistre(conn, sid, data, user=None):
    old = conn.execute("SELECT statut, date_cloture FROM sinistres WHERE id = ?", (sid,)).fetchone()
    if not old:
        raise NotFound("Sinistre introuvable.")
    s = clean_sinistre(data)
    if s["statut"] in STATUTS_CLOS and old["date_cloture"] and not data.get("date_cloture"):
        s["date_cloture"] = old["date_cloture"]
    s["updated_at"] = now_iso()
    conn.execute(f"UPDATE sinistres SET {', '.join(c + ' = ?' for c in s)} WHERE id = ?",
                 [*s.values(), sid])
    if old["statut"] != s["statut"]:
        add_journal(conn, sid, "Changement de statut", f"{old['statut']} → {s['statut']}",
                    auteur_id=user and user["id"])


def delete_sinistre_files(conn, sid):
    shutil.rmtree(os.path.join(DOCS_DIR, str(sid)), ignore_errors=True)


def compute_stats(conn):
    today = date.today().isoformat()
    sinistres = list_sinistres(conn)
    ouverts = [s for s in sinistres if s["statut"] not in STATUTS_CLOS]
    par_statut = {st: 0 for st in STATUTS}
    par_type = {}
    for s in sinistres:
        par_statut[s["statut"]] = par_statut.get(s["statut"], 0) + 1
        par_type[s["type"]] = par_type.get(s["type"], 0) + 1
    en_retard = [s for s in ouverts if s["date_echeance"] and s["date_echeance"] < today]
    a_venir = sorted((s for s in ouverts if s["date_echeance"] and s["date_echeance"] >= today),
                     key=lambda s: s["date_echeance"])[:10]
    sans_suivi = []
    for s in ouverts:
        last = s["derniere_action"] or s["created_at"][:10]
        if (date.today() - date.fromisoformat(last[:10])).days > 30:
            sans_suivi.append(s)
    return {
        "total": len(sinistres),
        "ouverts": len(ouverts),
        "clos": len(sinistres) - len(ouverts),
        "par_statut": par_statut,
        "par_type": par_type,
        "montant_estime_ouverts": sum(s["montant_estime"] or 0 for s in ouverts),
        "montant_indemnise_total": sum(s["montant_indemnise"] or 0 for s in sinistres),
        "en_retard": en_retard,
        "a_venir": a_venir,
        "sans_suivi": sans_suivi,
    }


CSV_COLUMNS = [
    ("reference", "Référence"), ("copropriete_nom", "Copropriété"), ("type", "Type"),
    ("statut", "Statut"), ("date_survenance", "Date survenance"),
    ("date_constat", "Date constat"), ("date_declaration", "Date déclaration"),
    ("localisation", "Localisation"), ("parties", "Parties"), ("declarant", "Déclarant"),
    ("declarant_contact", "Contact déclarant"), ("tiers", "Tiers / responsable"),
    ("description", "Description"), ("assureur", "Assureur"),
    ("numero_sinistre_assureur", "N° sinistre assureur"), ("convention", "Convention"),
    ("expert", "Expert"), ("date_expertise", "Date expertise"), ("entreprise", "Entreprise"),
    ("montant_estime", "Montant estimé"), ("montant_devis", "Montant devis"),
    ("franchise", "Franchise"), ("montant_indemnise", "Montant indemnisé"),
    ("date_indemnisation", "Date indemnisation"), ("prochaine_action", "Prochaine action"),
    ("date_echeance", "Échéance"), ("date_cloture", "Date clôture"),
    ("nb_documents", "Nb documents"),
]


def export_csv(conn):
    buf = io.StringIO()
    buf.write("﻿")  # BOM : Excel lit correctement les accents
    w = csv.writer(buf, delimiter=";")
    w.writerow([label for _, label in CSV_COLUMNS])
    for s in list_sinistres(conn):
        row = []
        for key, _ in CSV_COLUMNS:
            v = s.get(key)
            if key in MONEY_FIELDS and v is not None:
                v = f"{v:.2f}".replace(".", ",")
            row.append("" if v is None else v)
        w.writerow(row)
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Routage de l'API
# --------------------------------------------------------------------------- #
ROUTES = []


def route(method, pattern, public=False, admin=False):
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn, public, admin))
        return fn
    return deco


class Raw:
    """Réponse non-JSON (fichier, CSV…)."""

    def __init__(self, body=None, ctype="application/octet-stream", headers=None, path=None,
                 cleanup=None):
        self.body, self.ctype, self.headers, self.path = body, ctype, headers or {}, path
        self.cleanup = cleanup  # dossier temporaire à supprimer après l'envoi


class Ctx:
    def __init__(self, handler, conn, query):
        self.h, self.conn, self.query = handler, conn, query
        self.user = None
        self.headers = []  # en-têtes supplémentaires (Set-Cookie…)

    @property
    def uid(self):
        return self.user["id"] if self.user else None

    def client_ip(self):
        if TRUST_PROXY and self.h.headers.get("X-Forwarded-For"):
            return self.h.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.h.client_address[0]

    def json(self):
        length = int(self.h.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > 2 * 1024 * 1024:
            raise ApiError("Requête trop volumineuse.")
        try:
            data = json.loads(self.h.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError("Requête JSON invalide.")
        if not isinstance(data, dict):
            raise ApiError("Requête JSON invalide.")
        return data


# -- Authentification --
def user_count(conn):
    return conn.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0]


@route("GET", "/api/session", public=True)
def api_session(ctx):
    user = session_user(ctx.conn, ctx.h.cookie("sid"))
    return {"utilisateur": user, "installation": user_count(ctx.conn) == 0}


@route("POST", "/api/installation", public=True)
def api_setup(ctx):
    """Création du premier compte administrateur (uniquement si aucun compte n'existe)."""
    if user_count(ctx.conn):
        raise ApiError("L'application est déjà configurée.", HTTPStatus.FORBIDDEN)
    data = ctx.json()
    u = clean_user({**data, "role": "admin"}, creating=True)
    check_password_strength(data.get("mot_de_passe"))
    cur = ctx.conn.execute(
        "INSERT INTO utilisateurs (identifiant, nom, email, role, mot_de_passe, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (u["identifiant"], u["nom"], u["email"], "admin", hash_password(data["mot_de_passe"]),
         now_iso()))
    token = create_session(ctx.conn, cur.lastrowid)
    ctx.headers.append(("Set-Cookie", session_cookie(token, SESSION_HOURS * 3600)))
    return {"utilisateur": session_user(ctx.conn, token)}, HTTPStatus.CREATED


@route("POST", "/api/connexion", public=True)
def api_login(ctx):
    data = ctx.json()
    ident = str(data.get("identifiant") or "").strip().lower()
    key = (ctx.client_ip(), ident)
    if THROTTLE.blocked(key):
        raise ApiError("Trop de tentatives. Réessayez dans un quart d'heure.",
                       HTTPStatus.TOO_MANY_REQUESTS)
    row = ctx.conn.execute("SELECT id, mot_de_passe, actif FROM utilisateurs WHERE identifiant = ?",
                           (ident,)).fetchone()
    ok = verify_password(str(data.get("mot_de_passe") or ""),
                         row["mot_de_passe"] if row else DUMMY_HASH)
    if not (row and ok and row["actif"]):
        THROTTLE.fail(key)
        raise ApiError("Identifiant ou mot de passe incorrect.", HTTPStatus.UNAUTHORIZED)
    THROTTLE.reset(key)
    token = create_session(ctx.conn, row["id"])
    ctx.headers.append(("Set-Cookie", session_cookie(token, SESSION_HOURS * 3600)))
    return {"utilisateur": session_user(ctx.conn, token)}


@route("POST", "/api/deconnexion")
def api_logout(ctx):
    ctx.conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                     (token_hash(ctx.h.cookie("sid") or ""),))
    ctx.headers.append(("Set-Cookie", session_cookie("", 0)))
    return {"ok": True}


@route("PUT", "/api/moi")
def api_me_update(ctx):
    data = ctx.json()
    nom = str(data.get("nom") or "").strip()
    if not nom:
        raise ApiError("Le nom est obligatoire.")
    ctx.conn.execute("UPDATE utilisateurs SET nom = ?, email = ? WHERE id = ?",
                     (nom, str(data.get("email") or "").strip(), ctx.uid))
    if data.get("nouveau_mot_de_passe"):
        stored = ctx.conn.execute("SELECT mot_de_passe FROM utilisateurs WHERE id = ?",
                                  (ctx.uid,)).fetchone()[0]
        if not verify_password(str(data.get("ancien_mot_de_passe") or ""), stored):
            raise ApiError("Mot de passe actuel incorrect.")
        check_password_strength(data["nouveau_mot_de_passe"])
        ctx.conn.execute("UPDATE utilisateurs SET mot_de_passe = ? WHERE id = ?",
                         (hash_password(data["nouveau_mot_de_passe"]), ctx.uid))
        # Déconnecte les autres appareils
        ctx.conn.execute("DELETE FROM sessions WHERE utilisateur_id = ? AND token_hash != ?",
                         (ctx.uid, token_hash(ctx.h.cookie("sid"))))
    return {"ok": True}


# -- Utilisateurs --
@route("GET", "/api/utilisateurs")
def api_users(ctx):
    return [dict(r) for r in ctx.conn.execute(
        f"SELECT {USER_PUBLIC} FROM utilisateurs ORDER BY actif DESC, nom COLLATE NOCASE")]


@route("POST", "/api/utilisateurs", admin=True)
def api_user_create(ctx):
    data = ctx.json()
    u = clean_user(data, creating=True)
    check_password_strength(data.get("mot_de_passe"))
    try:
        cur = ctx.conn.execute(
            "INSERT INTO utilisateurs (identifiant, nom, email, role, mot_de_passe, created_at) "
            "VALUES (?,?,?,?,?,?)", (u["identifiant"], u["nom"], u["email"], u["role"],
                                     hash_password(data["mot_de_passe"]), now_iso()))
    except sqlite3.IntegrityError:
        raise ApiError("Cet identifiant est déjà utilisé.")
    return {"id": cur.lastrowid}, HTTPStatus.CREATED


@route("PUT", r"/api/utilisateurs/(\d+)", admin=True)
def api_user_update(ctx, uid):
    if not ctx.conn.execute("SELECT 1 FROM utilisateurs WHERE id = ?", (uid,)).fetchone():
        raise NotFound("Utilisateur introuvable.")
    data = ctx.json()
    u = clean_user(data, creating=False)
    actif = 1 if data.get("actif", True) else 0
    if (u["role"] != "admin" or not actif) and not count_admins(ctx.conn, exclude=uid):
        raise ApiError("Il doit rester au moins un administrateur actif.")
    ctx.conn.execute("UPDATE utilisateurs SET nom = ?, email = ?, role = ?, actif = ? WHERE id = ?",
                     (u["nom"], u["email"], u["role"], actif, uid))
    if data.get("mot_de_passe"):
        check_password_strength(data["mot_de_passe"])
        ctx.conn.execute("UPDATE utilisateurs SET mot_de_passe = ? WHERE id = ?",
                         (hash_password(data["mot_de_passe"]), uid))
    if data.get("mot_de_passe") or not actif:
        ctx.conn.execute("DELETE FROM sessions WHERE utilisateur_id = ?", (uid,))
    return {"ok": True}


# -- Sauvegarde complète (base + documents) --
@route("GET", "/api/sauvegarde.zip", admin=True)
def api_backup(ctx):
    tmpdir = tempfile.mkdtemp(prefix="sinistres-")
    db_copy = os.path.join(tmpdir, "sinistres.db")
    ctx.conn.commit()  # la copie attendrait sinon la fin de la transaction en cours
    dest = sqlite3.connect(db_copy)
    ctx.conn.backup(dest)
    dest.close()
    zpath = os.path.join(tmpdir, "sauvegarde.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db_copy, "sinistres.db")
        for root, _, files in os.walk(DOCS_DIR):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join("documents", os.path.relpath(full, DOCS_DIR)))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return Raw(path=zpath, ctype="application/zip", cleanup=tmpdir, headers={
        "Content-Disposition": f'attachment; filename="sauvegarde-sinistres-{stamp}.zip"'})


# -- Paramètres du cabinet --
def get_parametres(conn):
    out = {k: "" for k in PARAMETRES}
    for r in conn.execute("SELECT cle, valeur FROM parametres"):
        out[r["cle"]] = r["valeur"]
    return out


@route("GET", "/api/parametres")
def api_params(ctx):
    return get_parametres(ctx.conn)


@route("PUT", "/api/parametres", admin=True)
def api_params_update(ctx):
    data = ctx.json()
    for k in PARAMETRES:
        if k in data:
            ctx.conn.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
                             "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
                             (k, str(data[k] or "").strip()[:2000]))
    return get_parametres(ctx.conn)


@route("GET", "/api/meta")
def api_meta(ctx):
    return {"types": TYPES, "statuts": STATUTS, "statuts_clos": sorted(STATUTS_CLOS),
            "journal_types": JOURNAL_TYPES, "doc_categories": DOC_CATEGORIES,
            "max_upload": MAX_UPLOAD}


@route("GET", "/api/stats")
def api_stats(ctx):
    return compute_stats(ctx.conn)


@route("GET", "/api/export.csv")
def api_export(ctx):
    return Raw(export_csv(ctx.conn), "text/csv; charset=utf-8", {
        "Content-Disposition": f'attachment; filename="sinistres-{date.today().isoformat()}.csv"'})


# -- Copropriétés --
@route("GET", "/api/coproprietes")
def api_copros(ctx):
    return list_coproprietes(ctx.conn)


@route("POST", "/api/coproprietes")
def api_copro_create(ctx):
    c = clean_copro(ctx.json())
    cur = ctx.conn.execute(
        f"INSERT INTO coproprietes ({','.join(c)}) VALUES ({','.join('?' * len(c))})",
        list(c.values()))
    return {"id": cur.lastrowid}, HTTPStatus.CREATED


@route("PUT", r"/api/coproprietes/(\d+)")
def api_copro_update(ctx, cid):
    c = clean_copro(ctx.json())
    cur = ctx.conn.execute(f"UPDATE coproprietes SET {', '.join(k + ' = ?' for k in c)} WHERE id = ?",
                           [*c.values(), cid])
    if not cur.rowcount:
        raise NotFound()
    return {"ok": True}


@route("DELETE", r"/api/coproprietes/(\d+)", admin=True)
def api_copro_delete(ctx, cid):
    if not ctx.conn.execute("DELETE FROM coproprietes WHERE id = ?", (cid,)).rowcount:
        raise NotFound()
    return {"ok": True}


# -- Sinistres --
@route("GET", "/api/sinistres")
def api_sinistres(ctx):
    return list_sinistres(ctx.conn)


@route("POST", "/api/sinistres")
def api_sinistre_create(ctx):
    sid = create_sinistre(ctx.conn, ctx.json(), ctx.user)
    return get_sinistre(ctx.conn, sid), HTTPStatus.CREATED


@route("GET", r"/api/sinistres/(\d+)")
def api_sinistre(ctx, sid):
    return get_sinistre(ctx.conn, sid)


@route("PUT", r"/api/sinistres/(\d+)")
def api_sinistre_update(ctx, sid):
    update_sinistre(ctx.conn, sid, ctx.json(), ctx.user)
    return get_sinistre(ctx.conn, sid)


@route("DELETE", r"/api/sinistres/(\d+)", admin=True)
def api_sinistre_delete(ctx, sid):
    if not ctx.conn.execute("DELETE FROM sinistres WHERE id = ?", (sid,)).rowcount:
        raise NotFound()
    delete_sinistre_files(ctx.conn, sid)
    return {"ok": True}


# -- Journal --
@route("POST", r"/api/sinistres/(\d+)/journal")
def api_journal_add(ctx, sid):
    require_sinistre(ctx.conn, sid)
    d = ctx.json()
    commentaire = str(d.get("commentaire") or "").strip()
    jtype = d.get("type") or "Note"
    if not commentaire:
        raise ApiError("Le commentaire est obligatoire.")
    if jtype not in JOURNAL_TYPES:
        raise ApiError("Type d'entrée invalide.")
    add_journal(ctx.conn, sid, jtype, commentaire, check_date(d.get("date"), "date") or None,
                ctx.uid)
    ctx.conn.execute("UPDATE sinistres SET updated_at = ? WHERE id = ?", (now_iso(), sid))
    return get_sinistre(ctx.conn, sid), HTTPStatus.CREATED


@route("DELETE", r"/api/journal/(\d+)")
def api_journal_delete(ctx, jid):
    if not ctx.conn.execute("DELETE FROM journal WHERE id = ?", (jid,)).rowcount:
        raise NotFound()
    return {"ok": True}


# -- Documents --
def safe_ext(name):
    ext = os.path.splitext(name)[1].lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,8}", ext) else ""


@route("POST", r"/api/sinistres/(\d+)/documents")
def api_document_upload(ctx, sid):
    """Envoi d'un fichier brut. En-têtes : X-Filename (encodé URI), X-Categorie."""
    require_sinistre(ctx.conn, sid)
    h = ctx.h.headers
    length = int(h.get("Content-Length") or 0)
    if length <= 0:
        raise ApiError("Fichier vide.")
    if length > MAX_UPLOAD:
        raise ApiError(f"Fichier trop volumineux (maximum {MAX_UPLOAD // (1024 * 1024)} Mo).",
                       HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    nom = os.path.basename(unquote(h.get("X-Filename") or "").replace("\\", "/")).strip()[:200]
    nom = re.sub(r"[\x00-\x1f]", "", nom) or "document"
    categorie = unquote(h.get("X-Categorie") or "Autre")
    if categorie not in DOC_CATEGORIES:
        raise ApiError("Catégorie de document invalide.")
    mime = (h.get("Content-Type") or "").split(";")[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime) or mime == "application/json":
        mime = mimetypes.guess_type(nom)[0] or "application/octet-stream"

    folder = os.path.join(DOCS_DIR, str(sid))
    os.makedirs(folder, exist_ok=True)
    fichier = f"{sid}/{secrets.token_hex(16)}{safe_ext(nom)}"
    path = os.path.join(DOCS_DIR, fichier)
    remaining = length
    try:
        with open(path, "wb") as f:
            while remaining > 0:
                chunk = ctx.h.rfile.read(min(65536, remaining))
                if not chunk:
                    raise ApiError("Envoi interrompu.")
                f.write(chunk)
                remaining -= len(chunk)
        cur = ctx.conn.execute(
            "INSERT INTO documents (sinistre_id, nom, categorie, mime, taille, fichier, created_at, "
            "auteur_id) VALUES (?,?,?,?,?,?,?,?)",
            (sid, nom, categorie, mime, length, fichier, now_iso(), ctx.uid))
        add_journal(ctx.conn, sid, "Document", f"Document ajouté ({categorie}) : {nom}",
                    auteur_id=ctx.uid)
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise
    return {"id": cur.lastrowid, "nom": nom}, HTTPStatus.CREATED


def get_document(conn, did):
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        raise NotFound("Document introuvable.")
    return row


@route("GET", r"/api/documents/(\d+)")
def api_document_get(ctx, did):
    d = get_document(ctx.conn, did)
    path = os.path.join(DOCS_DIR, d["fichier"])
    if not os.path.isfile(path):
        raise NotFound("Fichier absent du disque.")
    inline = d["mime"] in INLINE_MIME and "download" not in ctx.query
    disposition = "inline" if inline else "attachment"
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", d["nom"])
    return Raw(path=path, ctype=d["mime"] if inline else "application/octet-stream", headers={
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; "
                               f"filename*=UTF-8''{quote(d['nom'])}"})


@route("PUT", r"/api/documents/(\d+)")
def api_document_update(ctx, did):
    d = get_document(ctx.conn, did)
    data = ctx.json()
    categorie = data.get("categorie", d["categorie"])
    nom = str(data.get("nom") or d["nom"]).strip()[:200]
    if categorie not in DOC_CATEGORIES:
        raise ApiError("Catégorie de document invalide.")
    ctx.conn.execute("UPDATE documents SET categorie = ?, nom = ? WHERE id = ?", (categorie, nom, did))
    return {"ok": True}


@route("DELETE", r"/api/documents/(\d+)")
def api_document_delete(ctx, did):
    d = get_document(ctx.conn, did)
    ctx.conn.execute("DELETE FROM documents WHERE id = ?", (did,))
    add_journal(ctx.conn, d["sinistre_id"], "Document", f"Document supprimé : {d['nom']}",
                auteur_id=ctx.uid)
    try:
        os.remove(os.path.join(DOCS_DIR, d["fichier"]))
    except FileNotFoundError:
        pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Serveur HTTP
# --------------------------------------------------------------------------- #
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                               "object-src 'self'; frame-src 'self'; frame-ancestors 'self'",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if not os.environ.get("SINISTRES_QUIET"):
            super().log_message(fmt, *args)

    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    def cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def send_json(self, payload, status=HTTPStatus.OK, headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_raw(self, raw):
        size = os.path.getsize(raw.path) if raw.path else len(raw.body)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", raw.ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, no-store")
        for k, v in raw.headers.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            if raw.path:
                with open(raw.path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self.wfile.write(raw.body)
        finally:
            if raw.cleanup:
                shutil.rmtree(raw.cleanup, ignore_errors=True)

    def dispatch(self, method):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        if not path.startswith("/api/"):
            if method == "GET":
                return super().do_GET()
            return self.send_json({"erreur": "Introuvable"}, HTTPStatus.NOT_FOUND)

        for m, regex, fn, public, admin in ROUTES:
            match = regex.match(path)
            if match and m == method:
                break
        else:
            return self.send_json({"erreur": "Introuvable"}, HTTPStatus.NOT_FOUND)

        # Protection CSRF : les navigateurs n'ajoutent pas cet en-tête lors d'une requête
        # provenant d'un autre site (sans autorisation CORS, que ce serveur ne donne jamais).
        if method != "GET" and self.headers.get("X-Requested-With") != "fetch":
            self.close_connection = True
            return self.send_json({"erreur": "Requête refusée."}, HTTPStatus.FORBIDDEN)

        conn = connect()
        ctx = Ctx(self, conn, parse_qs(url.query))
        try:
            if not public:
                ctx.user = session_user(conn, self.cookie("sid"))
                if not ctx.user:
                    raise ApiError("Session expirée : veuillez vous reconnecter.",
                                   HTTPStatus.UNAUTHORIZED)
                if admin and ctx.user["role"] != "admin":
                    raise ApiError("Action réservée aux administrateurs.", HTTPStatus.FORBIDDEN)
            result = fn(ctx, *(int(g) for g in match.groups()))
            conn.commit()
        except ApiError as e:
            conn.rollback()
            self.close_connection = True
            extra = {"auth": True} if e.status == HTTPStatus.UNAUTHORIZED else {}
            return self.send_json({"erreur": str(e), **extra}, e.status)
        except Exception as e:  # pragma: no cover
            conn.rollback()
            self.log_error("Erreur : %r", e)
            return self.send_json({"erreur": "Erreur interne du serveur."},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            conn.close()

        if isinstance(result, Raw):
            return self.send_raw(result)
        status = HTTPStatus.OK
        if isinstance(result, tuple):
            result, status = result
        self.send_json(result, status, ctx.headers)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    host = os.environ.get("SINISTRES_HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("SINISTRES_PORT", 8000))
    init_db()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Suivi des sinistres : http://localhost:{port}  (Ctrl+C pour arrêter)")
    if host not in ("127.0.0.1", "localhost"):
        print(f"Accessible depuis le réseau sur le port {port} (adresse d'écoute : {host}).")
    print(f"Base de données : {DB_PATH}")
    print(f"Documents       : {DOCS_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
