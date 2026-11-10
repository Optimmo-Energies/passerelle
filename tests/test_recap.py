"""Mail récapitulatif : collecte des analyses et appel unique à dpe_ingest."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import recap  # noqa: E402
import send  # noqa: E402

ANALYSE = {"lettre_dpe": "E", "ban_x": 1, "ecarts": [
    {"type": "SANS_PORTE", "classification": "false_improvement"}]}
REPONSE = {"status": "accepted", "analysed": 1,
           "results": [{"filename": "x.xml", "analysis": ANALYSE}]}


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._body = body
        self.text = text or json.dumps(body or {})
        self.ok = 200 <= code < 300

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class TestCollecte(unittest.TestCase):
    def test_deliver_garde_la_reponse(self):
        cfg = {"demo_mode": False, "api_url": "https://x/dpe_en_cours/upload"}
        with mock.patch.object(send.requests, "post", return_value=Resp(200, REPONSE)):
            r = send._deliver(b"zip", "f.zip", {"dossier": "D"}, cfg)
        self.assertTrue(r.startswith("Envoyé avec succès"))
        self.assertEqual(r.reponse["analysed"], 1)
        a = recap.analyses(r)
        self.assertEqual(a[0]["lettre_dpe"], "E")
        self.assertNotIn("ban_x", a[0])

    def test_reponse_non_json(self):
        cfg = {"demo_mode": False, "api_url": "https://x/upload"}
        with mock.patch.object(send.requests, "post", return_value=Resp(200, None, "ok")):
            r = send._deliver(b"zip", "f.zip", {"dossier": "D"}, cfg)
        self.assertEqual(recap.analyses(r), [])
        self.assertEqual(recap.analyses("texte simple"), [])

    def test_lot_transmis_et_echec(self):
        lot = recap.Lot()
        r = send.Transmission("ok", REPONSE)
        lot.transmis({"dossier": "D1", "surface": 80, "annee_construction": "1970",
                      "adresse": "1 rue"}, "LICIEL", r, ["remarque", ""])
        lot.echec("D2", "Analys'immo", RuntimeError("HTTP 502"))
        self.assertEqual(len(lot), 2)
        it = lot.items[0]
        self.assertEqual(it["resume"], {"surface": 80, "annee_construction": "1970", "adresse": "1 rue"})
        self.assertEqual(it["remarques"], ["remarque"])
        self.assertEqual(it["analyses"][0]["lettre_dpe"], "E")
        self.assertEqual(lot.items[1]["statut"], "erreur")
        self.assertIn("502", lot.items[1]["erreur"])

    def test_url(self):
        self.assertEqual(
            recap.recap_url({"api_url": "https://h/dpe_en_cours/upload"}),
            "https://h/dpe_en_cours/recap_email")
        self.assertEqual(recap.recap_url({"recap_email_url": "https://o/r",
                                          "api_url": "x"}), "https://o/r")


class TestEnvoi(unittest.TestCase):
    def _lot(self):
        lot = recap.Lot()
        lot.transmis({"dossier": "A"}, "LICIEL", send.Transmission("ok", REPONSE))
        lot.transmis({"dossier": "B"}, "LICIEL", send.Transmission("ok", REPONSE))
        return lot

    def test_un_seul_appel_pour_le_lot(self):
        cfg = {"demo_mode": False, "api_url": "https://h/dpe_en_cours/upload"}
        with mock.patch("auth.valid_access_token", return_value="tok"), \
                mock.patch.object(recap.requests, "post",
                                  return_value=Resp(200, {"status": "sent"})) as p, \
                mock.patch.object(recap, "_toast"):
            recap.dispatch(self._lot(), cfg, wait=True)
        self.assertEqual(p.call_count, 1)
        args, kw = p.call_args
        self.assertEqual(args[0], "https://h/dpe_en_cours/recap_email")
        self.assertEqual([d["dossier"] for d in kw["json"]["dossiers"]], ["A", "B"])
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tok")

    def test_404_pas_de_nouvelle_tentative(self):
        cfg = {"demo_mode": False, "api_url": "https://h/upload"}
        with mock.patch("auth.valid_access_token", return_value="tok"), \
                mock.patch.object(recap.requests, "post",
                                  return_value=Resp(404, {"detail": "Not Found"})) as p, \
                mock.patch.object(recap, "LOG_FILE", Path(tempfile.mkdtemp()) / "r.log"):
            self.assertFalse(recap._post(cfg, self._lot().items))
        self.assertEqual(p.call_count, 1)

    def test_erreur_reseau_ne_leve_jamais(self):
        cfg = {"demo_mode": False, "api_url": "https://h/upload"}
        with mock.patch("auth.valid_access_token", return_value="tok"), \
                mock.patch.object(recap.requests, "post",
                                  side_effect=recap.requests.ConnectionError("x")), \
                mock.patch.object(recap.time, "sleep"), \
                mock.patch.object(recap, "_toast"), \
                mock.patch.object(recap, "LOG_FILE", Path(tempfile.mkdtemp()) / "r.log"):
            recap.dispatch(self._lot(), cfg, wait=True)

    def test_demo_ecrit_la_charge_utile(self):
        out = tempfile.mkdtemp()
        with mock.patch.object(recap, "_toast"):
            recap.dispatch(self._lot(), {"demo_mode": True, "output_dir": out},
                           wait=True)
        f = next(Path(out).glob("recap_*.json"))
        self.assertEqual(len(json.loads(f.read_text(encoding="utf-8"))["dossiers"]), 2)

    def test_desactivable_et_lot_vide(self):
        with mock.patch.object(recap.threading, "Thread") as t:
            recap.dispatch(recap.Lot(), {"demo_mode": True})
            recap.dispatch(self._lot(), {"recap_email": False})
        t.assert_not_called()


class TestTray(unittest.TestCase):
    def test_envoi_consigne_les_echecs_mais_pas_la_reconnexion(self):
        import auth
        import tray
        lot = recap.Lot()
        with self.assertRaises(ValueError):
            tray._envoi(lot, "D1", "Analys'immo",
                        lambda: (_ for _ in ()).throw(ValueError("calcul non lancé")))
        with self.assertRaises(auth.ReauthRequired):
            tray._envoi(lot, "D2", "LICIEL",
                        lambda: (_ for _ in ()).throw(auth.ReauthRequired("x")))
        self.assertEqual([i["dossier"] for i in lot.items], ["D1"])


if __name__ == "__main__":
    unittest.main()
