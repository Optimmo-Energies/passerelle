"""Droit « passerelle » : lecture dans le jeton et blocage avant envoi."""
import base64
import json
import unittest
from unittest import mock

import auth
import tray


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"e30.{body.decode()}.sig"


def _tokens(claims):
    return mock.patch.object(auth, "_load_tokens",
                             return_value=(_jwt(claims), "refresh"))


class LectureDuDroit(unittest.TestCase):
    def test_droit_present(self):
        with _tokens({"scopes": ["espace_pro", "passerelle"]}):
            self.assertTrue(auth.has_passerelle_access())

    def test_droit_absent(self):
        with _tokens({"scopes": ["espace_pro", "opticheck"]}):
            self.assertFalse(auth.has_passerelle_access())

    def test_jeton_illisible_laisse_le_serveur_trancher(self):
        with mock.patch.object(auth, "_load_tokens",
                               return_value=("pas-un-jwt", "refresh")):
            self.assertTrue(auth.has_passerelle_access())
        with _tokens({"sub": "1"}):
            self.assertTrue(auth.has_passerelle_access())


class BlocageAvantEnvoi(unittest.TestCase):
    cfg = {"require_auth": True}

    def test_sans_droit_reconnexion_proposee_puis_refus(self):
        icon = mock.Mock()
        with mock.patch.object(auth, "is_authenticated", return_value=True), \
                mock.patch.object(auth, "valid_access_token", return_value="t"), \
                mock.patch.object(auth, "has_passerelle_access",
                                  return_value=False), \
                mock.patch.object(tray, "_propose_reconnect",
                                  return_value=True) as reco, \
                mock.patch.object(tray.dialog, "show_message") as msg:
            self.assertFalse(tray._ensure_authenticated(icon, self.cfg))
        reco.assert_called_once_with(icon, self.cfg, auth.NO_ACCESS_MESSAGE)
        msg.assert_called_once_with(auth.NO_ACCESS_MESSAGE)

    def test_droit_accorde_entre_temps_passe_apres_reconnexion(self):
        with mock.patch.object(auth, "is_authenticated", return_value=True), \
                mock.patch.object(auth, "valid_access_token", return_value="t"), \
                mock.patch.object(auth, "has_passerelle_access",
                                  side_effect=[False, True]), \
                mock.patch.object(tray, "_propose_reconnect", return_value=True), \
                mock.patch.object(tray.dialog, "show_message") as msg:
            self.assertTrue(tray._ensure_authenticated(mock.Mock(), self.cfg))
        msg.assert_not_called()

    def test_avec_droit_aucun_dialogue(self):
        with mock.patch.object(auth, "is_authenticated", return_value=True), \
                mock.patch.object(auth, "valid_access_token", return_value="t"), \
                mock.patch.object(auth, "has_passerelle_access",
                                  return_value=True), \
                mock.patch.object(tray, "_propose_reconnect") as reco:
            self.assertTrue(tray._ensure_authenticated(mock.Mock(), self.cfg))
        reco.assert_not_called()


if __name__ == "__main__":
    unittest.main()
