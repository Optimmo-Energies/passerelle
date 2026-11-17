"""
Champs qu'Analys'immo laisse vides et qu'Opticheck refusait émis à vide :
tout le DPE était rejeté (« Le fichier XML contient une valeur invalide »).
Cas réels : LEAL 39378, 32935 MORELLE (épaisseur du mur), KERVELLA 39578
(nombre de niveaux d'un chauffage d'appoint).
"""
import unittest
from unittest import mock

import ademe_adn

def _ctx():
    ctx = mock.MagicMock()
    ctx.logement = {"surfaceHabitable": 80}
    ctx.sortie, ctx.sortie_dep = {}, {}
    for nom in ("env", "tv_or_nil", "enum_or_nil", "adjacence", "reference",
                "methode_calcul_conso"):
        getattr(ctx, nom).return_value = "1"
    return ctx


class TestEpaisseurMur(unittest.TestCase):
    def _mur(self, row):
        with mock.patch.object(ademe_adn, "_de_paroi"), \
                mock.patch.object(ademe_adn, "_orientation", return_value="1"):
            return ademe_adn.build_mur(_ctx(), row)

    def test_omise_si_non_saisie(self):
        for vide in (None, ""):
            mur = self._mur({"epaisseurParoiSaisie": vide})
            self.assertIsNone(mur.find("donnee_entree/epaisseur_structure"))

    def test_emise_si_saisie(self):
        mur = self._mur({"epaisseurParoiSaisie": 22.5})
        self.assertEqual(mur.findtext("donnee_entree/epaisseur_structure"), "22.5")


class TestNombreNiveauxChauffage(unittest.TestCase):
    def test_un_niveau_par_defaut(self):
        for vide in (None, "", 0, "0", "abc"):
            self.assertEqual(ademe_adn._nombre_niveaux(vide), "1")

    def test_valeur_saisie(self):
        self.assertEqual(ademe_adn._nombre_niveaux(3), "3")
        self.assertEqual(ademe_adn._nombre_niveaux("2.0"), "2")


if __name__ == "__main__":
    unittest.main()
