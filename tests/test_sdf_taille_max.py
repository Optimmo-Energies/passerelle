"""
Base .sdf de plus de 256 Mo (limite par défaut de SQL Server Compact) :
la Passerelle doit l'ouvrir. Cas réel : Ex'im, base de synchro d'un bureau
actif → « taille du fichier supérieure à la taille maximale configurée ».
"""
import unittest

import adn_db


class TestChaineSdf(unittest.TestCase):
    def test_taille_max_relevee(self):
        cs = adn_db._sdf_connection_string(r"C:\x\ADN_DIAG.sdf")
        self.assertIn("Max Database Size=4091;", cs)
        self.assertIn("Mode=Read Only;", cs)


if __name__ == "__main__":
    unittest.main()
