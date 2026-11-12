"""
Choix de la base Analys'immo : la plus à jour, pas la première qui répond.

Cas réel : Analys'immo fermé, sa base principale ne répondait plus comme
avant et la Passerelle retenait une copie de synchro vide → « aucun dossier
avec mission DPE » alors que l'utilisateur en avait.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import adn_db


def _root_avec_sdf() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "ADN.exe").write_bytes(b"")
    sdf = root / "Synchro" / "SDL1" / "ADN_DIAG.sdf"
    sdf.parent.mkdir(parents=True)
    sdf.write_bytes(b"")
    return root


class TestConnectChoisitLaPlusRecente(unittest.TestCase):
    def setUp(self):
        adn_db._last_ok.clear()
        adn_db._localdb_cache.clear()
        adn_db._localdb_cache.update({"v": ["ADN_Local_Synchro_SDL1"], "t": 1e12})

    def tearDown(self):
        adn_db._last_ok.clear()
        adn_db._localdb_cache.clear()

    def _connect(self, dates: dict):
        root = _root_avec_sdf()
        with mock.patch.object(adn_db.SqlServerSource, "probe", lambda s: None), \
             mock.patch.object(adn_db.SdfSource, "probe", lambda s: None), \
             mock.patch.object(adn_db, "sqlce_available", return_value=True), \
             mock.patch.object(adn_db, "fraicheur",
                               side_effect=lambda src, database="": dates[src.kind]):
            return adn_db.connect({"adn_root": str(root)})

    def test_sdf_plus_recente_que_localdb(self):
        src = self._connect({"sqlserver": "2026-01-01T00:00:00",
                             "sdf": "2026-11-12T10:00:00"})
        self.assertEqual(src.kind, "sdf")

    def test_localdb_plus_recente_que_sdf_vide(self):
        src = self._connect({"sqlserver": "2026-11-12T10:00:00", "sdf": None})
        self.assertEqual(src.kind, "sqlserver")

    def test_sans_date_ordre_de_priorite(self):
        src = self._connect({"sqlserver": None, "sdf": None})
        self.assertEqual(src.kind, "sqlserver")


class TestResolveDbPlusieursBases(unittest.TestCase):
    def test_base_diag_la_plus_recente_et_meme_suffixe(self):
        src = adn_db.SqlServerSource("Data Source=x;", "x")
        noms = ["master", "ADN_DIAG", "ADN_DIAG_SDL1", "ADN_DIAG_DPE2012",
                "ADN_DIAG_DPE2012_SDL1"]
        dates = {"ADN_DIAG": None, "ADN_DIAG_SDL1": "2026-11-12T10:00:00"}

        def query(sql, database="", params=None):
            if "sys.databases" in sql:
                return [{"name": n} for n in noms]
            raise AssertionError(sql)

        with mock.patch.object(src, "query", side_effect=query), \
             mock.patch.object(adn_db, "fraicheur",
                               side_effect=lambda s, database="": dates[database]):
            self.assertEqual(src.resolve_db(adn_db.DB_DIAG), "ADN_DIAG_SDL1")
            self.assertEqual(src.resolve_db(adn_db.DB_DPE), "ADN_DIAG_DPE2012_SDL1")


if __name__ == "__main__":
    unittest.main()
