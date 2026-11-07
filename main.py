"""
Passerelle Optimmo – outil d'aide au diagnostiqueur DPE.
Lance une icône en barre des tâches permettant d'envoyer
le dossier LICIEL / Analys'immo actif à Optimmo pour analyse avant validation.

Option en ligne de commande (support / validation du build) :
    PasserelleOptimmo.exe --diagnostic-adn [fichier_rapport.txt]
écrit le rapport de diagnostic Analys'immo puis quitte, sans icône.
"""
import sys


def _diagnostic(argv: list[str]) -> int:
    import config
    import adn_diag
    dest = argv[0] if argv else None
    try:
        path = adn_diag.write_report(config.load(), dest)
    finally:
        try:
            import adn_db
            adn_db.cleanup_snapshots()
        except Exception:
            pass
    if not dest:
        import os
        os.startfile(str(path))  # noqa: S606 — ouverture du rapport pour l'utilisateur
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--diagnostic-adn":
        sys.exit(_diagnostic(sys.argv[2:]))

    import single_instance
    import tray

    if single_instance.acquire():
        tray.run()
    else:
        single_instance.notify_already_running()
