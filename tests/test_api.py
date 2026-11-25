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
        app.init_db()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                return r.status, (json.loads(raw) if "json" in ctype else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

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
        self.assertRegex(s["reference"], r"^SIN-\d{4}-0001$")
        self.assertEqual(s["montant_estime"], 1250.5)
        self.assertEqual(s["copropriete_nom"], "Résidence Les Tilleuls")
        self.assertEqual(len(s["journal"]), 1)
        sid = s["id"]

        st, s2 = self.call("POST", "/api/sinistres", {"type": "Incendie", "statut": "À déclarer"})
        self.assertTrue(s2["reference"].endswith("0002"))

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
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["ouverts"], 1)
        self.assertEqual(stats["montant_indemnise_total"], 1100)

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

    def test_page_accueil(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"Suivi des sinistres", r.read())


if __name__ == "__main__":
    unittest.main()
