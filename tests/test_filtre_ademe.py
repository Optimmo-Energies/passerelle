import unittest

from dialog import sans_numero_ademe


class TestFiltreSansNumeroAdeme(unittest.TestCase):
    def test_sans_numero(self):
        for v in (None, "", "—", "-", "  "):
            self.assertTrue(sans_numero_ademe({"numero_ademe": v}), v)
        self.assertTrue(sans_numero_ademe({}))

    def test_avec_numero(self):
        self.assertFalse(sans_numero_ademe({"numero_ademe": "2683E2174419X"}))


if __name__ == "__main__":
    unittest.main()
