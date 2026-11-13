"""
Connexion Passerelle : la page du navigateur ne dit « Connexion réussie » que si
les jetons ont vraiment été obtenus. Cas réel (Ex'im, 13/11) : la page affichait
« réussie » alors que la Passerelle restait déconnectée, boutons grisés.
"""
import threading
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import auth

CFG = {"auth_api_url": "https://auth.test", "webapp_url": "https://web.test"}


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


def _run_login(post_response, timeout=10):
    """Lance login() ; le faux navigateur suit le retour vers la Passerelle."""
    pages, threads = [], []

    def fake_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        cb = (f"{q['redirect_uri'][0]}?code=abc&state="
              f"{urllib.parse.quote(q['state'][0])}")

        def go():
            with urllib.request.urlopen(cb, timeout=10) as r:
                pages.append(r.read().decode("utf-8"))
        t = threading.Thread(target=go, daemon=True)
        t.start()
        threads.append(t)
        return True

    with mock.patch.object(auth.webbrowser, "open", fake_browser), \
         mock.patch.object(auth.requests, "post", return_value=post_response), \
         mock.patch.object(auth, "_store_tokens") as store, \
         mock.patch.object(auth, "_log"):
        ok = auth.login(CFG, timeout=timeout)
    for t in threads:
        t.join(10)
    return ok, pages, store


class TestRetourReel(unittest.TestCase):
    def test_succes(self):
        ok, pages, store = _run_login(
            _Resp(200, {"access_token": "a", "refresh_token": "r"}))
        self.assertTrue(ok)
        store.assert_called_once_with("a", "r")
        self.assertIn("Connexion réussie", pages[0])
        self.assertIsNone(auth.last_error())

    def test_echange_refuse(self):
        ok, pages, store = _run_login(_Resp(400, {"detail": "Invalid code"}))
        self.assertFalse(ok)
        store.assert_not_called()
        self.assertNotIn("Connexion réussie", pages[0])
        self.assertIn("Connexion impossible", pages[0])
        self.assertIn("HTTP 400 : Invalid code", auth.last_error())

    def test_navigateur_jamais_revenu(self):
        with mock.patch.object(auth.webbrowser, "open", return_value=True), \
             mock.patch.object(auth, "_log"):
            ok = auth.login(CFG, timeout=1)
        self.assertFalse(ok)
        self.assertIn("pas revenu vers la Passerelle", auth.last_error())


if __name__ == "__main__":
    unittest.main()
