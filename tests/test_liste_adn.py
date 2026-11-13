import unittest
from unittest import mock

import adn


class FakeSrc:
    """Base ADN minimale : 12 dossiers ; le dossier 12 (le plus récent) a 2 DPE."""

    def __init__(self):
        self.queries = []

    def resolve_db(self, logical):
        return logical

    def query(self, sql, database="", params=None):
        self.queries.append(sql)
        if "SELECT m.idMission" in sql:
            ms = [{"idMission": 100 + i, "idDossier": i, "intitule": "DPE",
                   "idCategorieMission": "DPE2021"} for i in range(12, 0, -1)]
            ms.insert(1, {"idMission": 200, "idDossier": 12, "intitule": "Lot 2",
                          "idCategorieMission": "DPE2021"})
            return ms
        if "FROM Dossier d" in sql:
            self.top = "TOP(" in sql
            return [{"idDossier": i, "reference": f"D{i}", "adresse": "1 rue",
                     "ville": "Nîmes", "surface": 50, "dateMaj": None}
                    for i in range(12, 0, -1)]
        raise AssertionError(sql)


def _complet(s, d, m):
    return {"dossier": d["reference"], "numero_ademe": "X", "classe_energie": "D"}


class TestListeAdn(unittest.TestCase):
    def test_resume_complet_pour_les_dpe_des_5_dossiers_recents(self):
        src = FakeSrc()
        numeros = {100 + i: ("2683E00000%02dX" % i if i % 2 else "")
                   for i in range(1, 13)}
        with mock.patch.object(adn, "_numeros_ademe", return_value=numeros), \
             mock.patch.object(adn, "parse_dpe_summary",
                               side_effect=_complet) as complet:
            out = adn.dossiers_avec_resume(src, limit=30, resume_dossiers=5)
        # 13 DPE : une ligne par mission, y compris le 2e DPE du dossier 12.
        self.assertEqual(len(out), 13)
        # Dossiers 12 (2 DPE), 11, 10, 9, 8 → 6 résumés complets.
        self.assertEqual(complet.call_count, 6)
        self.assertEqual({o["path"] for o in out[:2]}, {"ADN:12/112", "ADN:12/200"})
        self.assertTrue(out[1]["dossier"].endswith("Lot 2"))
        self.assertEqual(out[6]["classe_energie"], "—")      # dossier 7
        self.assertEqual(out[6]["numero_ademe"], numeros[107])
        self.assertEqual(out[7]["numero_ademe"], "—")         # dossier 6
        # Une seule requête missions pour toute la liste.
        self.assertEqual(sum("SELECT m.idMission" in q for q in src.queries), 1)
        self.assertTrue(src.top)

    def test_tous_les_dossiers_sans_resume(self):
        src = FakeSrc()
        with mock.patch.object(adn, "_numeros_ademe", return_value={}), \
             mock.patch.object(adn, "parse_dpe_summary",
                               side_effect=_complet) as complet:
            out = adn.dossiers_avec_resume(src, limit=None, resume_dossiers=0)
        self.assertEqual(len(out), 13)
        self.assertEqual(complet.call_count, 0)
        self.assertFalse(src.top)


if __name__ == "__main__":
    unittest.main()
