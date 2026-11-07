"""Installation au démarrage d'une mise à jour déjà téléchargée."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402

EXE = b"MZ" + b"x" * 1000


def _setup(version="9.9.9", marker=True, sha=None, tentatives=0):
    d = Path(tempfile.mkdtemp())
    cur = d / "PasserelleOptimmo (6).exe"
    cur.write_bytes(EXE)
    new = d / f".update-{version}.exe"
    new.write_bytes(EXE + b"new")
    if marker:
        (d / f".update-{version}.json").write_text(json.dumps({
            "version": version, "tentatives": tentatives,
            "sha256": sha or hashlib.sha256(new.read_bytes()).hexdigest()}),
            encoding="utf-8")
    return d, cur, new


class TestPendingAtStart(unittest.TestCase):
    def test_maj_verifiee_retenue_et_compteur(self):
        d, cur, new = _setup()
        self.assertEqual(updater.pending_at_start(cur), new)
        meta = json.loads((d / ".update-9.9.9.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["tentatives"], 1)

    def test_sans_empreinte_supprimee(self):
        d, cur, new = _setup(marker=False)  # cas des téléchargements 1.9.1
        self.assertIsNone(updater.pending_at_start(cur))
        self.assertFalse(new.exists())

    def test_empreinte_fausse_supprimee(self):
        d, cur, new = _setup(sha="0" * 64)
        self.assertIsNone(updater.pending_at_start(cur))
        self.assertFalse(new.exists())

    def test_pas_de_boucle_apres_deux_tentatives(self):
        d, cur, new = _setup(tentatives=2)
        self.assertIsNone(updater.pending_at_start(cur))
        self.assertFalse(new.exists())

    def test_version_ancienne_nettoyee(self):
        d, cur, new = _setup(version="1.7.0")
        self.assertIsNone(updater.pending_at_start(cur))
        self.assertFalse(new.exists())
        self.assertFalse((d / ".update-1.7.0.json").exists())

    def test_apply_programme_le_remplacement(self):
        d, cur, new = _setup()
        with mock.patch.object(updater.sys, "frozen", True, create=True), \
                mock.patch.object(updater.sys, "executable", str(cur)), \
                mock.patch.object(updater, "_stage_replacement") as stage:
            self.assertTrue(updater.apply_pending_at_start())
        stage.assert_called_once_with(new, cur)

    def test_apply_rien_en_dev(self):
        self.assertFalse(updater.apply_pending_at_start())

    def test_dossier_non_inscriptible(self):
        d, cur, new = _setup()
        with mock.patch.object(updater.sys, "frozen", True, create=True), \
                mock.patch.object(updater.sys, "executable", str(cur)), \
                mock.patch.object(updater, "_dir_writable", return_value=False), \
                mock.patch.object(updater, "_stage_replacement") as stage:
            self.assertFalse(updater.apply_pending_at_start())
        stage.assert_not_called()


class TestScript(unittest.TestCase):
    def test_bat_relance_meme_en_cas_d_echec(self):
        d, cur, new = _setup()
        bat = Path(tempfile.gettempdir()) / "optimmo_update.bat"
        with mock.patch.object(updater.subprocess, "Popen"):
            updater._stage_replacement(new, cur)
        s = bat.read_text(encoding="utf-8")
        self.assertIn("goto relaunch", s)
        self.assertIn(f'start "" "{cur}"', s)
        self.assertIn(".update-9.9.9.json", s)
        self.assertNotIn("echo ", s.replace("@echo off", ""))


if __name__ == "__main__":
    unittest.main()
