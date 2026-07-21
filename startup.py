"""
Démarrage automatique de la Passerelle à l'ouverture de session Windows.

On utilise la clé de registre HKCU\\...\\Run (par-utilisateur, sans droits admin).
Fonctionne aussi bien en mode développement (pythonw main.py) qu'en exécutable
PyInstaller figé (OptimmoPasserelle.exe).
"""
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "OptimmoPasserelle"


def _launch_command() -> str:
    """Commande à inscrire au démarrage, quotée pour les chemins avec espaces."""
    if getattr(sys, "frozen", False):
        # Exécutable PyInstaller : on relance directement le .exe.
        return f'"{sys.executable}"'
    # Mode script : pythonw (sans console) sur main.py.
    pyw = Path(sys.executable).with_name("pythonw.exe")
    runner = pyw if pyw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().with_name("main.py")
    return f'"{runner}" "{main_py}"'


def is_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> None:
    """Inscrit la Passerelle au démarrage de la session (idempotent)."""
    import winreg
    cmd = _launch_command()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)


def disable() -> None:
    """Retire la Passerelle du démarrage."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _APP_NAME)
    except FileNotFoundError:
        pass


def ensure(enabled: bool = True) -> None:
    """
    Aligne l'état du démarrage auto sur `enabled`. Met aussi à jour la commande
    si le chemin de l'exécutable a changé (ex : nouvelle version installée).
    """
    if not sys.platform.startswith("win"):
        return
    try:
        if enabled:
            enable()
        else:
            disable()
    except OSError:
        # On ne fait jamais planter l'app pour un souci de registre.
        pass
