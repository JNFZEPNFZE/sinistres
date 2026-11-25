import http.cookiejar
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SINISTRES_QUIET"] = "1"
import app  # noqa: E402


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB_PATH = os.path.join(cls.tmp.name, "test.db")
        app.DOCS_DIR = os.path.join(cls.tmp.name, "docs")
        app.init_db()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.opener = cls.new_opener()
        st, _ = cls.call(cls, "POST", "/api/installation", {
            "identifiant": "admin", "nom": "Admin Test", "mot_de_passe": "motdepasse-admin"})
        assert st == 201, st

    @staticmethod
    def new_opener():
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, raw=None, headers=None, opener=None):
        data = json.dumps(body).encode() if body is not None else raw
        hdrs = {"Content-Type": "application/json", "X-Requested-With": "fetch"}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with (opener or self.opener).open(req) as r:
                content = r.read()
                self.last_headers = r.headers
                ctype = r.headers.get("Content-Type", "")
                return r.status, (json.loads(content) if "json" in ctype else content)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def upload(self, sid, name, content, mime, categorie="Photo"):
        from urllib.parse import quote
        return self.call("POST", f"/api/sinistres/{sid}/documents", raw=content, headers={
            "Content-Type": mime, "X-Filename": quote(name), "X-Categorie": quote(categorie)})

    def test_documents(self):
        st, s = self.call("POST", "/api/sinistres", {"type": "Bris de glace", "statut": "Déclaré"})
        sid = s["id"]
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        st, d = self.upload(sid, "photo hall été.png", png, "image/png")
        self.assertEqual(st, 201)
        st, body = self.call("GET", f"/api/documents/{d['id']}")
        self.assertEqual(body, png)
        self.assertEqual(self.last_headers["Content-Type"], "image/png")
        self.assertTrue(self.last_headers["Content-Disposition"].startswith("inline"))

        # Un fichier HTML n'est jamais servi tel quel (protection XSS)
        st, h = self.upload(sid, "../../piege.html", b"<script>alert(1)</script>", "text/html",
                            "Autre")
        self.assertEqual(st, 201)
        self.assertEqual(h["nom"], "piege.html")
        self.call("GET", f"/api/documents/{h['id']}")
        self.assertEqual(self.last_headers["Content-Type"], "application/octet-stream")
        self.assertTrue(self.last_headers["Content-Disposition"].startswith("attachment"))

        st, err = self.upload(sid, "x.pdf", b"%PDF", "application/pdf", "Inconnue")
        self.assertEqual(st, 400)

        st, full = self.call("GET", f"/api/sinistres/{sid}")
        self.assertEqual(len(full["documents"]), 2)
        self.assertEqual(full["nb_documents"], 2)
        self.assertTrue(any("Document ajouté" in j["commentaire"] for j in full["journal"]))

        st, _ = self.call("PUT", f"/api/documents/{d['id']}", {"categorie": "Constat amiable"})
        self.assertEqual(st, 200)
        st, _ = self.call("DELETE", f"/api/documents/{h['id']}")
        self.assertEqual(st, 200)
        folder = os.path.join(app.DOCS_DIR, str(sid))
        self.assertEqual(len(os.listdir(folder)), 1)
        self.call("DELETE", f"/api/sinistres/{sid}")
        self.assertFalse(os.path.exists(folder))

    def test_migration_base_v1(self):
        """Une base créée par la première version est mise à niveau sans perte."""
        import sqlite3
        path = os.path.join(self.tmp.name, "ancienne.db")
        conn = sqlite3.connect(path)
        conn.executescript(app.MIGRATIONS[0])
        conn.execute("INSERT INTO sinistres (reference, type, statut, created_at, updated_at) "
                     "VALUES ('SIN-2026-0001', 'Incendie', 'Déclaré', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()
        app.init_db(path)
        conn = app.connect(path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(app.MIGRATIONS))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sinistres").fetchone()[0], 1)
        conn.close()

    def test_parcours_complet(self):
        st, c = self.call("POST", "/api/coproprietes", {"nom": "Résidence Les Tilleuls",
                                                        "assureur": "AXA"})
        self.assertEqual(st, 201)
        cid = c["id"]

        st, s = self.call("POST", "/api/sinistres", {
            "copropriete_id": cid, "type": "Dégât des eaux", "statut": "Déclaré",
            "date_survenance": "2026-11-02", "montant_estime": "1 250,50",
            "localisation": "Bât. A, 3e étage", "date_echeance": "2000-01-01",
        })
        self.assertEqual(st, 201)
        self.assertRegex(s["reference"], r"^SIN-\d{4}-\d{4}$")
        num = int(s["reference"][-4:])
        self.assertEqual(s["montant_estime"], 1250.5)
        self.assertEqual(s["copropriete_nom"], "Résidence Les Tilleuls")
        self.assertEqual(len(s["journal"]), 1)
        sid = s["id"]

        st, s2 = self.call("POST", "/api/sinistres", {"type": "Incendie", "statut": "À déclarer"})
        self.assertEqual(int(s2["reference"][-4:]), num + 1)

        payload = dict(s, statut="Clôturé", montant_indemnise=1100)
        st, upd = self.call("PUT", f"/api/sinistres/{sid}", payload)
        self.assertEqual(st, 200)
        self.assertEqual(upd["statut"], "Clôturé")
        self.assertTrue(upd["date_cloture"])
        self.assertIn("Déclaré → Clôturé", [j["commentaire"] for j in upd["journal"]])

        st, upd = self.call("POST", f"/api/sinistres/{sid}/journal",
                            {"type": "Appel", "commentaire": "Appel expert", "date": "2026-11-10"})
        self.assertEqual(st, 201)
        self.assertEqual(len(upd["journal"]), 3)

        st, stats = self.call("GET", "/api/stats")
        self.assertGreaterEqual(stats["total"], 2)
        self.assertGreaterEqual(stats["montant_indemnise_total"], 1100)

        st, csv_bytes = self.call("GET", "/api/export.csv")
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("Référence;Copropriété", text)
        self.assertIn("1250,50", text)

        st, _ = self.call("DELETE", f"/api/coproprietes/{cid}")
        self.assertEqual(st, 200)
        st, s = self.call("GET", f"/api/sinistres/{sid}")
        self.assertIsNone(s["copropriete_id"])

        st, _ = self.call("DELETE", f"/api/sinistres/{sid}")
        self.assertEqual(st, 200)
        st, _ = self.call("GET", f"/api/sinistres/{sid}")
        self.assertEqual(st, 404)

    def test_validation(self):
        st, err = self.call("POST", "/api/sinistres", {"type": "Inconnu", "statut": "Déclaré"})
        self.assertEqual(st, 400)
        self.assertIn("Type", err["erreur"])
        st, err = self.call("POST", "/api/sinistres",
                            {"type": "Incendie", "statut": "Déclaré", "montant_estime": "abc"})
        self.assertEqual(st, 400)
        st, err = self.call("POST", "/api/coproprietes", {"nom": "  "})
        self.assertEqual(st, 400)

    def test_authentification(self):
        anonyme = self.new_opener()
        st, err = self.call("GET", "/api/sinistres", opener=anonyme)
        self.assertEqual(st, 401)
        self.assertTrue(err["auth"])
        st, ses = self.call("GET", "/api/session", opener=anonyme)
        self.assertEqual(ses, {"utilisateur": None, "installation": False})
        # Une seconde installation est refusée
        st, _ = self.call("POST", "/api/installation", {
            "identifiant": "pirate", "nom": "X", "mot_de_passe": "12345678"}, opener=anonyme)
        self.assertEqual(st, 403)
        # Protection CSRF : sans l'en-tête X-Requested-With, refus
        st, _ = self.call("POST", "/api/coproprietes", {"nom": "X"},
                          headers={"X-Requested-With": ""})
        self.assertEqual(st, 403)

        # Création d'un gestionnaire par l'admin
        st, _ = self.call("POST", "/api/utilisateurs", {
            "identifiant": "Marie", "nom": "Marie Martin", "role": "gestionnaire",
            "mot_de_passe": "court"})
        self.assertEqual(st, 400)
        st, u = self.call("POST", "/api/utilisateurs", {
            "identifiant": "Marie", "nom": "Marie Martin", "role": "gestionnaire",
            "mot_de_passe": "secret-marie"})
        self.assertEqual(st, 201)
        st, _ = self.call("POST", "/api/utilisateurs", {
            "identifiant": "marie", "nom": "Doublon", "mot_de_passe": "secret-marie"})
        self.assertEqual(st, 400)

        marie = self.new_opener()
        st, _ = self.call("POST", "/api/connexion",
                          {"identifiant": "marie", "mot_de_passe": "mauvais"}, opener=marie)
        self.assertEqual(st, 401)
        st, ses = self.call("POST", "/api/connexion",
                            {"identifiant": "MARIE", "mot_de_passe": "secret-marie"}, opener=marie)
        self.assertEqual(st, 200)
        self.assertEqual(ses["utilisateur"]["role"], "gestionnaire")

        # Le journal enregistre l'auteur
        st, s = self.call("POST", "/api/sinistres", {"type": "Incendie", "statut": "Déclaré"},
                          opener=marie)
        self.assertEqual(s["journal"][0]["auteur"], "Marie Martin")
        # Droits : un gestionnaire ne peut ni supprimer un dossier ni gérer les comptes
        st, _ = self.call("DELETE", f"/api/sinistres/{s['id']}", opener=marie)
        self.assertEqual(st, 403)
        st, _ = self.call("POST", "/api/utilisateurs", {"identifiant": "x"}, opener=marie)
        self.assertEqual(st, 403)
        st, _ = self.call("GET", "/api/sauvegarde.zip", opener=marie)
        self.assertEqual(st, 403)

        # Changement de mot de passe : l'ancien est exigé
        st, _ = self.call("PUT", "/api/moi", {"nom": "Marie M.", "ancien_mot_de_passe": "faux",
                                              "nouveau_mot_de_passe": "nouveau-secret"}, opener=marie)
        self.assertEqual(st, 400)
        st, _ = self.call("PUT", "/api/moi", {"nom": "Marie M.", "ancien_mot_de_passe": "secret-marie",
                                              "nouveau_mot_de_passe": "nouveau-secret"}, opener=marie)
        self.assertEqual(st, 200)

        # Désactivation : la session est coupée immédiatement
        st, _ = self.call("PUT", f"/api/utilisateurs/{u['id']}",
                          {"nom": "Marie M.", "role": "gestionnaire", "actif": False})
        self.assertEqual(st, 200)
        st, _ = self.call("GET", "/api/sinistres", opener=marie)
        self.assertEqual(st, 401)

        # Le dernier administrateur ne peut pas être rétrogradé
        st, users = self.call("GET", "/api/utilisateurs")
        admin = next(x for x in users if x["identifiant"] == "admin")
        self.assertNotIn("mot_de_passe", admin)
        st, err = self.call("PUT", f"/api/utilisateurs/{admin['id']}",
                            {"nom": "Admin", "role": "gestionnaire", "actif": True})
        self.assertEqual(st, 400)

        st, _ = self.call("POST", "/api/deconnexion", opener=marie)
        self.assertEqual(st, 401)

    def test_limitation_tentatives(self):
        o = self.new_opener()
        for _ in range(8):
            st, _ = self.call("POST", "/api/connexion",
                              {"identifiant": "admin", "mot_de_passe": "x"}, opener=o)
            self.assertEqual(st, 401)
        st, _ = self.call("POST", "/api/connexion",
                          {"identifiant": "admin", "mot_de_passe": "motdepasse-admin"}, opener=o)
        self.assertEqual(st, 429)
        app.THROTTLE.fails.clear()

    def test_sauvegarde(self):
        import zipfile
        st, s = self.call("POST", "/api/sinistres", {"type": "Autre", "statut": "Déclaré"})
        self.upload(s["id"], "devis.pdf", b"%PDF-1.4", "application/pdf", "Devis")
        st, body = self.call("GET", "/api/sauvegarde.zip")
        self.assertEqual(st, 200)
        z = zipfile.ZipFile(io.BytesIO(body))
        names = z.namelist()
        self.assertIn("sinistres.db", names)
        self.assertTrue(any(n.startswith(f"documents/{s['id']}/") for n in names))

    def test_parametres(self):
        st, p = self.call("PUT", "/api/parametres", {"cabinet_nom": "Cabinet Dupont",
                                                     "cabinet_ville": "Lyon", "inconnu": "x"})
        self.assertEqual(st, 200)
        self.assertEqual(p["cabinet_nom"], "Cabinet Dupont")
        self.assertNotIn("inconnu", p)
        st, p = self.call("GET", "/api/parametres")
        self.assertEqual(p["cabinet_ville"], "Lyon")
        st, c = self.call("POST", "/api/coproprietes", {"nom": "Le Parc",
                                                        "assureur_adresse": "1 rue X, Paris"})
        st, lst = self.call("GET", "/api/coproprietes")
        self.assertEqual(next(x for x in lst if x["id"] == c["id"])["assureur_adresse"],
                         "1 rue X, Paris")

    def test_champs_personnalises_et_gestionnaire(self):
        st, defs = self.call("PUT", "/api/champs", {"champs": [
            {"libelle": "Réf. syndic interne", "type": "texte"},
            {"libelle": "Urgence", "type": "liste", "options": "Faible, Moyenne,Haute"},
            {"libelle": "Copropriétaire informé", "type": "oui_non"},
            {"libelle": "Surface touchée (m²)", "type": "nombre"},
        ]})
        self.assertEqual(st, 200, defs)
        self.assertEqual(defs[0]["cle"], "ref_syndic_interne")
        self.assertEqual(defs[1]["options"], ["Faible", "Moyenne", "Haute"])
        st, err = self.call("PUT", "/api/champs", {"champs": [{"libelle": "X", "type": "liste"}]})
        self.assertEqual(st, 400)

        st, s = self.call("POST", "/api/sinistres", {
            "type": "Dégât des eaux", "statut": "Déclaré", "numero_lot": "24",
            "champs_perso": {"ref_syndic_interne": "A-12", "urgence": "Haute",
                             "coproprietaire_informe": True, "surface_touchee_m2": "3,5",
                             "pirate": "ignoré"}})
        self.assertEqual(st, 201, s)
        self.assertEqual(s["numero_lot"], "24")
        self.assertEqual(s["gestionnaire_nom"], "Admin Test")  # créateur par défaut
        self.assertEqual(s["champs_perso"]["urgence"], "Haute")
        self.assertEqual(s["champs_perso"]["surface_touchee_m2"], 3.5)
        self.assertNotIn("pirate", s["champs_perso"])
        self.assertIs(s["champs_perso"]["coproprietaire_informe"], True)

        st, err = self.call("PUT", f"/api/sinistres/{s['id']}",
                            dict(s, champs_perso={"urgence": "Inconnue"}))
        self.assertEqual(st, 400)
        st, err = self.call("PUT", f"/api/sinistres/{s['id']}", dict(s, gestionnaire_id=9999))
        self.assertEqual(st, 400)

        # Renommer un champ conserve sa clé (et donc les valeurs déjà saisies)
        defs[1]["libelle"] = "Niveau d'urgence"
        st, defs2 = self.call("PUT", "/api/champs", {"champs": defs})
        self.assertEqual(defs2[1]["cle"], "urgence")

        st, csv_bytes = self.call("GET", "/api/export.csv")
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("Niveau d'urgence", text.splitlines()[0])
        self.assertIn("Haute", text)

        st, stats = self.call("GET", f"/api/stats?gestionnaire={s['gestionnaire_id']}")
        self.assertGreaterEqual(stats["total"], 1)
        st, stats = self.call("GET", "/api/stats?gestionnaire=9999")
        self.assertEqual(stats["total"], 0)
        self.call("PUT", "/api/champs", {"champs": []})

    def test_page_accueil(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"Suivi des sinistres", r.read())


if __name__ == "__main__":
    unittest.main()
