"""
La Passerelle ne doit jamais empêcher Analys'immo d'ouvrir sa propre base.

Cas réel (Ex'im, 14/11/2026) : pendant la copie de travail d'une base .sdf de
plusieurs centaines de Mo, ADN affichait « Violation d'un partage de fichier »
et ne pouvait plus télétransmettre à l'ADEME.
"""
import ctypes
import os
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

import adn_db
import watcher


def _ouvrir_en_ecriture(path: str) -> int:
    """Ouvre comme le ferait ADN (lecture/écriture). Renvoie l'erreur Windows."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(path, 0xC0000000, 0x3, None, 3, 0x80, None)
    if h == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error()
    k32.CloseHandle(h)
    return 0


@unittest.skipUnless(os.name == "nt", "partage de fichiers Windows")
class TestCopiePartagee(unittest.TestCase):
    def test_adn_peut_ouvrir_sa_base_pendant_la_copie(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "ADN_DIAG.sdf"), os.path.join(d, "copie.sdf")
            bloc = os.urandom(1024 * 1024)
            with open(src, "wb") as f:
                for _ in range(300):
                    f.write(bloc)
            t = threading.Thread(target=adn_db._copie_partagee, args=(src, dst))
            t.start()
            erreurs = set()
            while t.is_alive():
                erreurs.add(_ouvrir_en_ecriture(src))
            t.join()
            self.assertEqual(erreurs - {0}, set())
            self.assertEqual(Path(dst).read_bytes(), Path(src).read_bytes())


class _Sdf:
    def __init__(self):
        self.empreinte = ((1, 1), (1, 1))

    def empreinte_fichiers(self):
        return self.empreinte


class TestSondageSdf(unittest.TestCase):
    """La base n'est interrogée (donc recopiée) qu'une fois les fichiers stables."""

    def setUp(self):
        self.src = _Sdf()
        self.rappels = []
        self.poller = watcher.AdnPoller({}, lambda: self.rappels.append(1))
        self.lectures = 0
        self.signature = "m1@t1"

        def signature():
            self.lectures += 1
            return self.signature
        self.poller._signature = signature
        p = mock.patch("adn.open_source", return_value=self.src)
        p.start()
        self.addCleanup(p.stop)

    def test_cycle_complet(self):
        tick = self.poller._tick
        tick()                                   # référence
        self.assertEqual(self.lectures, 1)
        tick(); tick()                           # rien ne bouge : aucune lecture
        self.assertEqual(self.lectures, 1)
        for n in (2, 3, 4):                      # saisie en cours : aucune lecture
            self.src.empreinte = ((n, n), (1, 1))
            self.signature = f"m1@t{n}"
            tick()
        self.assertEqual(self.lectures, 1)
        tick()                                   # stabilisé : lecture
        self.assertEqual((self.lectures, self.rappels), (2, []))
        tick()                                   # confirmation : rappel
        self.assertEqual(self.rappels, [1])
        tick(); tick()                           # puis plus rien
        self.assertEqual((self.lectures, self.rappels), (3, [1]))

    def test_sql_server_inchange(self):
        with mock.patch("adn.open_source", return_value=object()):
            self.poller._tick(); self.poller._tick()
        self.assertEqual(self.lectures, 2)


if __name__ == "__main__":
    unittest.main()
