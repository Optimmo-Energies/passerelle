"""
Empêche de lancer plusieurs instances de la Passerelle en parallèle.

Repose sur un mutex Windows nommé : le premier processus le crée et le garde
ouvert pour toute sa durée de vie ; tout second lancement le trouve déjà
existant et s'arrête. Windows relâche le mutex automatiquement à la
fermeture du processus (même en cas de crash) : aucun nettoyage à faire,
contrairement à un fichier de verrouillage qui pourrait rester bloqué.

Passage de relais à une nouvelle version : quand l'utilisateur lance un
exécutable situé ailleurs que celui déjà ouvert (typiquement la nouvelle
version tout juste téléchargée, alors qu'une ancienne tourne encore dans la
barre des tâches), l'ancienne instance est fermée et la nouvelle prend sa
place. Sans cela, l'utilisateur verrait « déjà ouverte » et continuerait, sans
le savoir, à utiliser l'ancienne version. Les versions antérieures à la 1.7
n'avaient pas de mutex et l'ancien nom « OptimmoPasserelle.exe » : elles sont
traitées de la même façon.
"""
import ctypes
import os
import re
import sys
import time

_MUTEX_NAME = "Global\\OptimmoEnergies_PasserelleOptimmo_SingleInstance"
_ERROR_ALREADY_EXISTS = 183
# Noms d'exécutable de la Passerelle, y compris les copies renommées par le
# navigateur au téléchargement (« PasserelleOptimmo (4).exe ») et l'ancien nom.
_EXE_NAME = re.compile(r"^(passerelle\s*optimmo|optimmo\s*passerelle).*\.exe$",
                       re.IGNORECASE)


def _is_passerelle(name: str) -> bool:
    return bool(_EXE_NAME.match(name or ""))

_handle = None  # gardé en vie tant que le process tourne (jamais fermé explicitement)


def _try_mutex() -> bool | None:
    """True = verrou pris, False = déjà détenu, None = mutex indisponible."""
    global _handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        h = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not h:
            return None
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(h))
            return False
        _handle = h
        return True
    except Exception:
        return None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _other_installs() -> list[dict]:
    """
    Passerelles ouvertes dans la session courante, lancées depuis un autre
    exécutable que celui-ci. Le processus parent (chargeur PyInstaller du même
    exe) et ce processus sont naturellement exclus par la comparaison de chemin.
    """
    if not getattr(sys, "frozen", False):
        return []
    try:
        import winproc
    except Exception:
        return []
    me = _norm(sys.executable)
    session = winproc.current_session()
    own = {os.getpid(), os.getppid()}
    out = []
    for p in winproc.find_processes(_is_passerelle):
        if p["pid"] in own:
            continue
        if session is not None and p.get("session") not in (None, session):
            continue  # autre utilisateur connecté sur le même poste
        path = _norm(p.get("path") or "")
        if path and path != me:
            out.append(p)
    return out


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


_state = {"newer_elsewhere": ""}


def _take_over() -> int:
    """
    Ferme les Passerelles lancées depuis un autre exécutable, à condition que
    celui-ci soit plus ancien que le nôtre (date du fichier : date du
    téléchargement ou de la dernière mise à jour). Une copie plus récente
    ouverte ailleurs est laissée en place : on ne remplace jamais une version
    à jour par un vieil exécutable relancé par erreur. Renvoie le nombre fermé.
    """
    try:
        import winproc
    except Exception:
        return 0
    mine = _mtime(sys.executable)
    n = 0
    for p in _other_installs():
        theirs = _mtime(p.get("path") or "")
        if theirs and theirs > mine + 60:
            _state["newer_elsewhere"] = p.get("path") or ""
            continue
        if winproc.terminate(p["pid"]):
            n += 1
    return n


def acquire() -> bool:
    """
    Tente de prendre le verrou. Renvoie True si cette instance est la seule
    en cours (démarrage normal), False si une autre tourne déjà depuis le
    même exécutable. Renvoie aussi True hors Windows ou en cas d'échec du
    mutex : on ne bloque jamais le démarrage pour ce garde-fou secondaire.
    """
    if not sys.platform.startswith("win"):
        return True

    # Ancienne version ouverte ailleurs (avec ou sans mutex) : on la ferme.
    try:
        _take_over()
    except Exception:
        pass

    # Le mutex d'une instance tout juste fermée peut mettre un instant à être
    # relâché par Windows : on réessaie quelques secondes.
    deadline = time.monotonic() + 6
    while True:
        got = _try_mutex()
        if got is None:
            return True
        if got:
            return True
        if time.monotonic() > deadline:
            return False
        # Une instance du même exe, ou une version plus récente installée
        # ailleurs, tourne vraiment : inutile d'attendre.
        if _state["newer_elsewhere"]:
            return False
        if not _other_installs() and _same_exe_running():
            return False
        time.sleep(0.5)


def _same_exe_running() -> bool:
    """Vrai si une autre Passerelle tourne depuis ce même exécutable."""
    if not getattr(sys, "frozen", False):
        return True  # en développement : comportement historique
    try:
        import winproc
    except Exception:
        return True
    me = _norm(sys.executable)
    own = {os.getpid(), os.getppid()}
    for p in winproc.find_processes(_is_passerelle):
        if p["pid"] in own:
            continue
        if _norm(p.get("path") or "") in (me, ""):
            return True
    return False


def notify_already_running() -> None:
    """Affiche un message expliquant que la Passerelle est déjà ouverte."""
    import dialog
    if _state["newer_elsewhere"]:
        dialog.show_message(
            "Une version plus récente de Passerelle Optimmo est déjà ouverte :\n"
            f"{_state['newer_elsewhere']}\n\n"
            "Elle reste en service (barre des tâches, à côté de l'horloge). "
            "Vous pouvez supprimer l'exécutable que vous venez d'ouvrir."
        )
        return
    dialog.show_message(
        "Passerelle Optimmo est déjà ouverte.\n\n"
        "Regardez dans la barre des tâches, à côté de l'horloge."
    )
