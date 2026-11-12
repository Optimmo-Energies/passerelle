import unittest
from types import SimpleNamespace
from unittest import mock

import ademe_adn


def _ctx(key):
    return SimpleNamespace(logement={"keyInertie": key}, src=None, dossier={})


class TestClasseInertie(unittest.TestCase):
    def test_cle_et_libelle(self):
        self.assertEqual(ademe_adn._classe_inertie(_ctx("TL"), (0, 0, 0)), "1")
        self.assertEqual(ademe_adn._classe_inertie(_ctx("Moyenne"), (0, 0, 0)), "3")

    def test_xml_officiel_si_cle_inconnue(self):
        xml = b"<dpe><inertie><enum_classe_inertie_id>2</enum_classe_inertie_id></inertie></dpe>"
        with mock.patch.object(ademe_adn.adn, "official_xml",
                               return_value=("0.xml", xml, "2026-01-01")):
            self.assertEqual(ademe_adn._classe_inertie(_ctx(""), (0, 0, 0)), "2")

    def test_regle_3cl_en_dernier_recours(self):
        with mock.patch.object(ademe_adn.adn, "official_xml", return_value=None):
            for lourds, attendu in (((1, 1, 1), "1"), ((1, 0, 1), "2"),
                                    ((0, 0, 1), "3"), ((0, 0, 0), "4")):
                self.assertEqual(
                    ademe_adn._classe_inertie(_ctx(None), lourds), attendu)


if __name__ == "__main__":
    unittest.main()
