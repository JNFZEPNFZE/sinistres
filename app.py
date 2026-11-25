#!/usr/bin/env python3
"""Suivi des sinistres en copropriété.

Serveur web local sans dépendance externe (bibliothèque standard Python + SQLite).
Lancement : python3 app.py  puis ouvrir http://localhost:8000
"""
import csv
import io
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.environ.get("SINISTRES_DB", os.path.join(BASE_DIR, "sinistres.db"))

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

SCHEMA = """
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
"""

COPRO_FIELDS = ["nom", "adresse", "assureur", "numero_police", "courtier", "notes"]
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
JOURNAL_TYPES = ["Note", "Appel", "Courriel", "Courrier", "Relance", "Expertise",
                 "Devis", "Travaux", "Règlement", "Changement de statut"]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(Exception):
    pass


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=None):
    with connect(path) as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def clean_copro(data):
    out = {f: str(data.get(f) or "").strip() for f in COPRO_FIELDS}
    if not out["nom"]:
        raise ValidationError("Le nom de la copropriété est obligatoire.")
    return out


def clean_sinistre(data):
    out = {}
    for f in SINISTRE_FIELDS:
        v = data.get(f)
        if f in MONEY_FIELDS:
            if v in (None, ""):
                out[f] = None
            else:
                try:
                    out[f] = round(float(str(v).replace(",", ".").replace(" ", "")), 2)
                except ValueError:
                    raise ValidationError(f"Montant invalide pour « {f} ».")
        elif f == "copropriete_id":
            out[f] = int(v) if v not in (None, "") else None
        else:
            out[f] = str(v or "").strip()
            if f in DATE_FIELDS and out[f] and not DATE_RE.match(out[f]):
                raise ValidationError(f"Date invalide pour « {f} » (format AAAA-MM-JJ).")
    if out["type"] not in TYPES:
        raise ValidationError("Type de sinistre invalide.")
    if out["statut"] not in STATUTS:
        raise ValidationError("Statut invalide.")
    if out["statut"] in STATUTS_CLOS and not out["date_cloture"]:
        out["date_cloture"] = date.today().isoformat()
    if out["statut"] not in STATUTS_CLOS:
        out["date_cloture"] = ""
    return out


# --------------------------------------------------------------------------- #
# Accès aux données
# --------------------------------------------------------------------------- #
def next_reference(conn):
    year = date.today().year
    prefix = f"SIN-{year}-"
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
           (SELECT MAX(j.date) FROM journal j WHERE j.sinistre_id = s.id) AS derniere_action
    FROM sinistres s LEFT JOIN coproprietes c ON c.id = s.copropriete_id
"""


def list_sinistres(conn):
    rows = conn.execute(SINISTRE_SELECT + " ORDER BY s.created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_sinistre(conn, sid):
    row = conn.execute(SINISTRE_SELECT + " WHERE s.id = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["journal"] = [dict(r) for r in conn.execute(
        "SELECT * FROM journal WHERE sinistre_id = ? ORDER BY date DESC, id DESC", (sid,))]
    return s


def add_journal(conn, sid, jtype, commentaire, jdate=None):
    conn.execute(
        "INSERT INTO journal (sinistre_id, date, type, commentaire, created_at) VALUES (?,?,?,?,?)",
        (sid, jdate or date.today().isoformat(), jtype, commentaire, now_iso()),
    )


def create_sinistre(conn, data):
    s = clean_sinistre(data)
    ts = now_iso()
    s["reference"] = next_reference(conn)
    s["created_at"] = s["updated_at"] = ts
    cols = list(s.keys())
    cur = conn.execute(
        f"INSERT INTO sinistres ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [s[c] for c in cols],
    )
    sid = cur.lastrowid
    add_journal(conn, sid, "Changement de statut", f"Dossier créé — statut : {s['statut']}")
    return sid


def update_sinistre(conn, sid, data):
    old = conn.execute("SELECT statut, date_cloture FROM sinistres WHERE id = ?", (sid,)).fetchone()
    if not old:
        return False
    s = clean_sinistre(data)
    if s["statut"] in STATUTS_CLOS and old["date_cloture"] and not data.get("date_cloture"):
        s["date_cloture"] = old["date_cloture"]
    s["updated_at"] = now_iso()
    conn.execute(
        f"UPDATE sinistres SET {', '.join(c + ' = ?' for c in s)} WHERE id = ?",
        [*s.values(), sid],
    )
    if old["statut"] != s["statut"]:
        add_journal(conn, sid, "Changement de statut", f"{old['statut']} → {s['statut']}")
    return True


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
    a_venir = sorted(
        (s for s in ouverts if s["date_echeance"] and s["date_echeance"] >= today),
        key=lambda s: s["date_echeance"],
    )[:10]
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
]


def export_csv(conn):
    buf = io.StringIO()
    buf.write("﻿")  # BOM pour qu'Excel lise correctement les accents
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
# Serveur HTTP
# --------------------------------------------------------------------------- #
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if os.environ.get("SINISTRES_QUIET"):
            return
        super().log_message(fmt, *args)

    # -- utilitaires --
    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            raise ValidationError("Requête JSON invalide.")

    def route(self, method):
        path = urlparse(self.path).path.rstrip("/")
        if not path.startswith("/api"):
            if method == "GET":
                return super().do_GET()
            return self.send_json({"erreur": "Introuvable"}, HTTPStatus.NOT_FOUND)
        try:
            with connect() as conn:
                result = self.api(conn, method, path)
            if result is None:
                self.send_json({"erreur": "Introuvable"}, HTTPStatus.NOT_FOUND)
        except ValidationError as e:
            self.send_json({"erreur": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:  # pragma: no cover
            self.send_json({"erreur": f"Erreur serveur : {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def api(self, conn, method, path):
        m = re.fullmatch(r"/api/(\w+)(?:/(\d+))?(?:/(\w+))?", path) or re.fullmatch(
            r"/api/(export\.csv)", path)
        if not m:
            return None
        resource = m.group(1)
        rid = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else None
        sub = m.group(3) if m.lastindex and m.lastindex >= 3 else None

        if resource == "meta" and method == "GET":
            return self.send_json({"types": TYPES, "statuts": STATUTS,
                                   "statuts_clos": sorted(STATUTS_CLOS),
                                   "journal_types": JOURNAL_TYPES}) or True

        if resource == "stats" and method == "GET":
            return self.send_json(compute_stats(conn)) or True

        if resource == "export.csv" and method == "GET":
            body = export_csv(conn)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="sinistres-{date.today().isoformat()}.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        if resource == "coproprietes":
            if method == "GET" and rid is None:
                return self.send_json(list_coproprietes(conn)) or True
            if method == "POST" and rid is None:
                c = clean_copro(self.read_json())
                cur = conn.execute(
                    f"INSERT INTO coproprietes ({','.join(c)}) VALUES ({','.join('?' * len(c))})",
                    list(c.values()))
                return self.send_json({"id": cur.lastrowid}, HTTPStatus.CREATED) or True
            if method == "PUT" and rid:
                c = clean_copro(self.read_json())
                cur = conn.execute(
                    f"UPDATE coproprietes SET {', '.join(k + ' = ?' for k in c)} WHERE id = ?",
                    [*c.values(), rid])
                return (self.send_json({"ok": True}) or True) if cur.rowcount else None
            if method == "DELETE" and rid:
                cur = conn.execute("DELETE FROM coproprietes WHERE id = ?", (rid,))
                return (self.send_json({"ok": True}) or True) if cur.rowcount else None

        if resource == "sinistres":
            if method == "GET" and rid is None:
                return self.send_json(list_sinistres(conn)) or True
            if method == "POST" and rid is None:
                sid = create_sinistre(conn, self.read_json())
                return self.send_json(get_sinistre(conn, sid), HTTPStatus.CREATED) or True
            if method == "GET" and rid and not sub:
                s = get_sinistre(conn, rid)
                return (self.send_json(s) or True) if s else None
            if method == "PUT" and rid and not sub:
                if not update_sinistre(conn, rid, self.read_json()):
                    return None
                return self.send_json(get_sinistre(conn, rid)) or True
            if method == "DELETE" and rid and not sub:
                cur = conn.execute("DELETE FROM sinistres WHERE id = ?", (rid,))
                return (self.send_json({"ok": True}) or True) if cur.rowcount else None
            if method == "POST" and rid and sub == "journal":
                if not conn.execute("SELECT 1 FROM sinistres WHERE id = ?", (rid,)).fetchone():
                    return None
                d = self.read_json()
                commentaire = str(d.get("commentaire") or "").strip()
                jtype = d.get("type") or "Note"
                jdate = str(d.get("date") or "").strip() or None
                if not commentaire:
                    raise ValidationError("Le commentaire est obligatoire.")
                if jtype not in JOURNAL_TYPES:
                    raise ValidationError("Type d'entrée invalide.")
                if jdate and not DATE_RE.match(jdate):
                    raise ValidationError("Date invalide.")
                add_journal(conn, rid, jtype, commentaire, jdate)
                conn.execute("UPDATE sinistres SET updated_at = ? WHERE id = ?", (now_iso(), rid))
                return self.send_json(get_sinistre(conn, rid), HTTPStatus.CREATED) or True

        if resource == "journal" and method == "DELETE" and rid:
            cur = conn.execute("DELETE FROM journal WHERE id = ?", (rid,))
            return (self.send_json({"ok": True}) or True) if cur.rowcount else None

        return None

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")


def main():
    host = os.environ.get("SINISTRES_HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("SINISTRES_PORT", 8000))
    init_db()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Suivi des sinistres : http://localhost:{port}  (Ctrl+C pour arrêter)")
    print(f"Base de données : {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
