import unittest
from unittest import mock

import adn


class FakeSrc:
    """Base ADN minimale : 12 dossiers, une mission DPE chacun."""

    def __init__(self):
        self.queries = []

    def resolve_db(self, logical):
        return logical

    def query(self, sql, database="", params=None):
        self.queries.append(sql)
        if "FROM Dossier d" in sql and "SELECT TOP" in sql:
            return [{"idDossier": i, "reference": f"D{i}", "adresse": "1 rue",
                     "ville": "Nîmes", "surface": 50, "dateMaj": None}
                    for i in range(12, 0, -1)]
        if "FROM Mission m" in sql:
            return [{"idMission": 100 + i, "idDossier": i,
                     "idCategorieMission": "DPE2021"} for i in range(1, 13)]
        raise AssertionError(sql)


class TestListeAdn(unittest.TestCase):
    def test_resume_complet_limite_et_numeros_pour_tous(self):
        src = FakeSrc()
        numeros = {100 + i: ("2683E00000%02dX" % i if i % 2 else "")
                   for i in range(1, 13)}
        with mock.patch.object(adn, "_numeros_ademe", return_value=numeros), \
             mock.patch.object(adn, "parse_dpe_summary",
                               side_effect=lambda s, d, m: {
                                   "dossier": d["reference"], "numero_ademe": "X",
                                   "classe_energie": "D"}) as complet:
            out = adn.dossiers_avec_resume(src, limit=200, resume_count=5)
        self.assertEqual(len(out), 12)
        self.assertEqual(complet.call_count, 5)
        self.assertEqual([o["classe_energie"] for o in out[:5]], ["D"] * 5)
        self.assertEqual(out[5]["classe_energie"], "—")
        # Dossier 7 (6e) : mission 107, numéro impair → renseigné.
        self.assertEqual(out[5]["numero_ademe"], numeros[107])
        # Dossier 6 (7e) : pas de numéro.
        self.assertEqual(out[6]["numero_ademe"], "—")
        self.assertEqual(out[6]["path"], "ADN:6/106")
        # Une seule requête missions pour toute la liste.
        self.assertEqual(sum("SELECT m.idMission" in q for q in src.queries), 1)


if __name__ == "__main__":
    unittest.main()
