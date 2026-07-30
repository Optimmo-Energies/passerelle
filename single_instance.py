"""
Empêche de lancer plusieurs instances de la Passerelle en parallèle.

Repose sur un mutex Windows nommé : le premier processus le crée et le garde
ouvert pour toute sa durée de vie ; tout second lancement le trouve déjà
existant et s'arrête. Windows relâche le mutex automatiquement à la
fermeture du processus (même en cas de crash) : aucun nettoyage à faire,
contrairement à un fichier de verrouillage qui pourrait rester bloqué.
"""
import ctypes
import sys

_MUTEX_NAME = "Global\\OptimmoEnergies_PasserelleOptimmo_SingleInstance"
_ERROR_ALREADY_EXISTS = 183

_handle = None  # gardé en vie tant que le process tourne (jamais fermé explicitement)


def acquire() -> bool:
    """
    Tente de prendre le verrou. Renvoie True si cette instance est la seule
    en cours (démarrage normal), False si une autre tourne déjà. Renvoie
    aussi True hors Windows ou en cas d'échec du mutex : on ne bloque jamais
    le démarrage de l'app pour un souci lié à ce garde-fou secondaire.
    """
    global _handle
    if not sys.platform.startswith("win"):
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not _handle:
            return True
        return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS
    except Exception:
        return True


def notify_already_running() -> None:
    """Affiche un message expliquant que la Passerelle est déjà ouverte."""
    import dialog
    dialog.show_message(
        "Passerelle Optimmo est déjà ouverte.\n\n"
        "Regardez dans la barre des tâches, à côté de l'horloge."
    )
