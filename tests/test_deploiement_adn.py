"""
Tests hors base (aucun Analys'immo requis) de la détection et de la
connexion ADN, de la migration de configuration et du XML officiel.

    python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adn  # noqa: E402
import adn_db  # noqa: E402
import config  # noqa: E402

SD_LOCALDB = r"""<?xml version="1.0"?>
<SDConfig><defaultSdsIndex>0</defaultSdsIndex><listeSds><SDS>
<Name>Local</Name><serverName></serverName><authentificationType>Windows</authentificationType>
<connectionName></connectionName><passWord></passWord>
<NameInstanceLocalDB>ADN_Local_Synchro_SDL20_20262304110948</NameInstanceLocalDB>
<IsLocalDB>true</IsLocalDB><SDLRelativeDirectory>Synchro\SDL20</SDLRelativeDirectory>
<portNumber>0</portNumber></SDS></listeSds></SDConfig>"""

SD_RESEAU = r"""<?xml version="1.0"?>
<SDConfig><defaultSdsIndex>1</defaultSdsIndex><listeSds>
<SDS><Name>Ancien</Name><serverName>OLD</serverName><IsLocalDB>false</IsLocalDB></SDS>
<SDS><Name>Cabinet</Name><serverName>SRV\SQLATLIBITUM</serverName>
<authentificationType>SqlServer</authentificationType><connectionName>adn</connectionName>
<passWord>secret</passWord><IsLocalDB>false</IsLocalDB><portNumber>1450</portNumber></SDS>
</listeSds></SDConfig>"""


class FakeSql(adn_db.SqlServerSource):
    def __init__(self, dbs):
        super().__init__("Data Source=x;")
        self.dbs = dbs

    def query(self, sql, database="", params=None):
        if "sys.databases" in sql:
            return [{"name": n} for n in self.dbs]
        return [{"idDossier": 1}]


class TestConnexion(unittest.TestCase):
    def setUp(self):
        adn_db._localdb_cache.clear()
        adn_db._localdb_cache["v"] = []

    def _sd(self, xml):
        d = tempfile.mkdtemp()
        Path(d, "sd.config").write_text(xml, encoding="utf-8")
        return d, adn_db.read_sd_config(d)

    def test_localdb(self):
        _, sd = self._sd(SD_LOCALDB)
        c = adn_db._candidates(sd)
        self.assertEqual(len(c), 1)
        self.assertIn(r"(localdb)\ADN_Local_Synchro_SDL20", c[0][1])

    def test_reseau_sql_port_windows(self):
        _, sd = self._sd(SD_RESEAU)
        labels = [l for l, _ in adn_db._candidates(sd)]
        cs = [c for _, c in adn_db._candidates(sd)]
        self.assertEqual(sd["server"], r"SRV\SQLATLIBITUM")
        self.assertTrue(any("User ID=adn;Password=secret" in x for x in cs))
        self.assertTrue(any("Data Source=SRV,1450;" in x for x in cs))
        self.assertTrue(any("authentification Windows" in l for l in labels))

    def test_instances_localdb_detectees(self):
        adn_db._localdb_cache["v"] = ["ADN_Local_X", "MSSQLLocalDB"]
        c = adn_db._candidates({})
        self.assertEqual([x[1] for x in c][0],
                         r"Data Source=(localdb)\ADN_Local_X;Integrated Security=True;")
        self.assertEqual(len(c), 2)

    def test_override_en_premier(self):
        c = adn_db._candidates({}, "Data Source=A;")
        self.assertEqual(c[0][1], "Data Source=A;")

    def test_probe_rejette_instance_sans_adn(self):
        with self.assertRaises(adn_db.AdnUnavailable):
            FakeSql(["master", "tempdb", "AutreLogiciel"]).probe()
        FakeSql(["master", "ADN_DIAG", "ADN_DIAG_DPE2012", "ADN_RG"]).probe()
        FakeSql(["master", "ADN_DIAG_SDL1", "ADN_DIAG_DPE2012_SDL1"]).probe()
        with self.assertRaises(adn_db.AdnUnavailable):
            FakeSql(["master", "ADN_DIAG_DPE2012"]).probe()

    def test_resolve_db_suffixe(self):
        src = FakeSql(["ADN_DIAG_DPE2012_SDL1", "ADN_DIAG_SDL1", "ADN_RG_SDL1"])
        self.assertEqual(src.resolve_db("ADN_DIAG"), "ADN_DIAG_SDL1")
        self.assertEqual(src.resolve_db("ADN_DIAG_DPE2012"), "ADN_DIAG_DPE2012_SDL1")

    def test_racine_hint_et_fallback(self):
        d = tempfile.mkdtemp()
        Path(d, "ADN.exe").write_bytes(b"")
        self.assertEqual(adn_db.find_adn_root(d, fallback=False), str(Path(d)))
        self.assertIsNone(adn_db.find_adn_root(d + "_absent", fallback=False))

    def test_racine_processus(self):
        d = tempfile.mkdtemp()
        Path(d, "ADN.exe").write_bytes(b"")
        adn_db._root_cache.clear()
        with mock.patch.object(adn_db, "_running_adn_dirs", return_value=[d]), \
                mock.patch.object(adn_db, "DEFAULT_ROOTS", ()), \
                mock.patch.object(adn_db, "_extra_default_roots", return_value=[]):
            self.assertEqual(adn_db.find_adn_root(""), str(Path(d)))
        adn_db._root_cache.clear()


class TestMigrationConfig(unittest.TestCase):
    def test_ancienne_config_passe_en_prod(self):
        d = Path(tempfile.mkdtemp())
        f = d / "config.json"
        f.write_text(json.dumps({"demo_mode": True, "require_auth": False,
                                 "api_url": "https://api.optimmo.example/x",
                                 "smtp_password": "garde",
                                 "diagnostiqueur": {"nom": "X"}}), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_FILE", f):
            cfg = config.load()
            self.assertFalse(cfg["demo_mode"])
            self.assertTrue(cfg["require_auth"])
            self.assertEqual(cfg["api_url"], config.DEFAULTS["api_url"])
            self.assertEqual(cfg["smtp_password"], "garde")
            self.assertEqual(cfg["diagnostiqueur"]["nom"], "X")
            self.assertIn("mail", cfg["diagnostiqueur"])
            # Une fois migrée, un choix ultérieur est respecté.
            cfg["demo_mode"] = True
            config.save(cfg)
            self.assertTrue(config.load()["demo_mode"])

    def test_config_corrompue(self):
        d = Path(tempfile.mkdtemp())
        f = d / "config.json"
        f.write_text("{pas du json", encoding="utf-8")
        with mock.patch.object(config, "CONFIG_FILE", f):
            cfg = config.load()
        self.assertFalse(cfg["demo_mode"])
        self.assertTrue((d / "config.json.corrompu").exists())


class TestXmlOfficiel(unittest.TestCase):
    def test_trouve_le_plus_recent(self):
        root = Path(tempfile.mkdtemp())
        guid = "1ff7c2fa-3ceb-4c13-8403-999363849dcc"
        std = root / "Reporting/Diagnostic/Dossier/2026/avril" / guid / "standard"
        std.mkdir(parents=True)
        (std / "0 (DPE 2021).xml").write_bytes(
            b'<?xml version="1.0" encoding="UTF-8"?><dpe version="2.4"></dpe>')
        (std / "autre.xml").write_bytes(b"<rapport/>")
        src = mock.Mock()
        src.root = str(root)
        off = adn.official_xml(src, {"idDossier": 1, "guidSdl": guid.upper()})
        self.assertEqual(off[0], "0 (DPE 2021).xml")
        self.assertIsNone(adn.official_xml(src, {"idDossier": 1, "guidSdl": "x"}))

    def test_categories_futures(self):
        clause = adn._categorie_clause("m", adn.DPE_CATEGORIES)
        self.assertIn("LIKE N'DPE%'", clause)
        self.assertNotIn("LIKE", adn._categorie_clause("m", adn.AUDIT_CATEGORIES))


class TestRepliXmlOfficiel(unittest.TestCase):
    def test_reconstruction_ko_xml_officiel_transmis(self):
        import ademe_adn
        import send
        off = ("0 (DPE 2021).xml", b"<dpe/>", "2026-11-07T10:00:00")
        with mock.patch.object(ademe_adn, "build", side_effect=RuntimeError("cas inédit")), \
                mock.patch.object(adn, "official_xml", return_value=off):
            ademe, rapport = send._try_ademe_adn(object(), {}, {}, {})
        self.assertEqual(ademe, ("officiel_adn_0_DPE_2021.xml", b"<dpe/>"))
        self.assertIn("cas inédit", rapport["erreur_reconstruction"])
        self.assertIn("repli", rapport)

        out = tempfile.mkdtemp()
        cfg = {"demo_mode": True, "output_dir": out}
        msg = send.send_adn_dpe({"dossier": "D1", "calcul_effectue": True},
                                {"meta": {}}, cfg, ademe, rapport, off)
        self.assertIn("[DÉMO]", msg)
        import zipfile
        z = zipfile.ZipFile(next(Path(out).glob("*.zip")))
        names = z.namelist()
        self.assertIn("DPE_ADEME/officiel_adn_0_DPE_2021.xml", names)
        self.assertIn("ADN/xml_officiel/officiel_adn_0_DPE_2021.xml", names)
        s = json.loads(z.read("summary.json"))
        self.assertEqual(s["xml_ademe_source"], "officiel_adn")

    def test_xml_officiel_perime_signale(self):
        import ademe_adn
        import send
        off = ("0 (DPE 2021).xml", b"<dpe/>", "2026-01-01T10:00:00")
        with mock.patch.object(ademe_adn, "build", side_effect=RuntimeError("x")),                 mock.patch.object(adn, "official_xml", return_value=off):
            _, rapport = send._try_ademe_adn(
                object(), {"dateMaj": "2026-03-01T09:00:00"}, {}, {})
        self.assertTrue(rapport["xml_officiel_anterieur_derniere_modif"])

    def test_config_diagnostiqueur_invalide(self):
        d = Path(tempfile.mkdtemp())
        f = d / "config.json"
        f.write_text(json.dumps({"diagnostiqueur": "texte"}), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_FILE", f):
            self.assertIn("nom", config.load()["diagnostiqueur"])

    def test_ordre_sdf_avant_localdb_generique(self):
        adn_db._localdb_cache.clear()
        adn_db._localdb_cache.update({"v": ["MSSQLLocalDB"], "t": 1e12})
        root = Path(tempfile.mkdtemp())
        (root / "ADN.exe").write_bytes(b"")
        sdf = root / "Synchro" / "SDL1" / "ADN_DIAG.sdf"
        sdf.parent.mkdir(parents=True)
        sdf.write_bytes(b"")
        order = []

        def fake_probe(self):
            order.append(getattr(self, "kind"))
            if self.kind == "sqlserver":
                raise adn_db.AdnUnavailable("non")

        with mock.patch.object(adn_db.SqlServerSource, "probe", fake_probe),                 mock.patch.object(adn_db.SdfSource, "probe", fake_probe),                 mock.patch.object(adn_db, "sqlce_available", return_value=True):
            src = adn_db.connect({"adn_root": str(root)})
        self.assertEqual(src.kind, "sdf")
        self.assertEqual(order, ["sdf"])
        adn_db._last_ok.clear()
        adn_db._localdb_cache.clear()


    def test_sans_calcul_ni_xml_refuse(self):
        import send
        with self.assertRaises(ValueError):
            send.send_adn_dpe({"dossier": "D1", "calcul_effectue": False},
                              {"meta": {}}, {"demo_mode": True,
                                             "output_dir": tempfile.mkdtemp()},
                              ("reconstruit_adn_D1.xml", b"<dpe/>"), {})


if __name__ == "__main__":
    unittest.main()
