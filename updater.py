"""
Mise à jour automatique de la Passerelle au démarrage.

Principe (simple et robuste pour un exécutable PyInstaller --onefile) :
  1. Au lancement, on lit un manifeste JSON distant (update_url) :
        { "version": "1.1.0",
          "url": "https://.../OptimmoPasserelle-1.1.0.exe",
          "notes": "..." }
  2. Si la version distante est plus récente que la version locale, on télécharge
     le nouvel .exe à côté de l'actuel, puis on programme le remplacement via un
     petit script .bat lancé au moment où l'on quitte (l'exe en cours étant
     verrouillé tant qu'il tourne).
  3. En mode développement (non figé), on se contente de signaler la version.

Aucune erreur réseau ne doit jamais faire planter l'application.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from version import __version__


def _parse(v: str) -> tuple:
    """Compare des versions sémantiques 'x.y.z' de façon numérique."""
    parts = []
    for chunk in str(v).strip().split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote: str, local: str | None = None) -> bool:
    # Lecture tardive de la version locale (et non figée à l'import).
    return _parse(remote) > _parse(local if local is not None else __version__)


def check_manifest(update_url: str, timeout: int = 8) -> dict | None:
    """Récupère le manifeste distant. Renvoie None si indisponible/illisible."""
    if not update_url:
        return None
    try:
        import json
        resp = requests.get(update_url, timeout=timeout)
        resp.raise_for_status()
        # utf-8-sig : tolère un éventuel BOM en tête de fichier (sinon json.loads
        # échoue → MAJ jamais détectée). Décode avec ou sans BOM indifféremment.
        data = json.loads(resp.content.decode("utf-8-sig"))
        if isinstance(data, dict) and data.get("version") and data.get("url"):
            return data
    except Exception:
        return None
    return None


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    """
    Télécharge `url` vers `dest` en vérifiant l'intégrité. Un fichier incomplet
    (réseau coupé, cache CDN tronqué) produirait un exe corrompu → « Failed to
    load Python DLL » au lancement. On vérifie donc la taille reçue contre
    Content-Length, et on lève en cas d'écart (le fichier partiel est supprimé).
    """
    written = 0
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("Content-Length", 0))
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
    if expected and written != expected:
        dest.unlink(missing_ok=True)
        raise IOError(
            f"Téléchargement incomplet : {written}/{expected} octets")
    # Un exe Windows valide commence par 'MZ' — garde-fou supplémentaire.
    with open(dest, "rb") as f:
        if f.read(2) != b"MZ":
            dest.unlink(missing_ok=True)
            raise IOError("Fichier téléchargé invalide (pas un exécutable)")


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _stage_replacement(new_exe: Path, current_exe: Path) -> None:
    """
    Écrit un .bat qui, une fois l'app fermée, remplace l'exe courant par le neuf
    et relance la Passerelle. Le .bat se supprime lui-même à la fin.
    """
    bat = Path(tempfile.gettempdir()) / "optimmo_update.bat"
    # IMPORTANT : aucun `echo` non redirigé. Le .bat est lancé sans console
    # (DETACHED_PROCESS) ; écrire sur une sortie console inexistante avorte tout
    # le script → la mise à jour ne s'installerait jamais. On retente le move un
    # nombre borné de fois (l'exe courant est verrouillé tant que l'app tourne).
    script = f"""@echo off
set /a tries=0
:wait
ping 127.0.0.1 -n 2 >nul
move /y "{new_exe}" "{current_exe}" >nul 2>&1
if not errorlevel 1 goto done
set /a tries+=1
if %tries% lss 30 goto wait
goto cleanup
:done
start "" "{current_exe}"
:cleanup
del "%~f0"
"""
    bat.write_text(script, encoding="utf-8")
    # CREATE_NEW_PROCESS_GROUP + détaché : survit à la fermeture de l'app.
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )


def check_and_prepare(cfg: dict) -> dict | None:
    """
    À appeler au démarrage. Renvoie un dict d'info si une mise à jour a été
    préparée (téléchargée et prête à s'installer à la fermeture), sinon None.

    {"version": "...", "notes": "...", "pending": True/False}
    """
    if not cfg.get("auto_update", True):
        return None

    manifest = check_manifest(cfg.get("update_url", ""))
    if not manifest or not is_newer(manifest["version"]):
        return None

    info = {
        "version": manifest["version"],
        "notes": manifest.get("notes", ""),
        "pending": False,
    }

    # En mode développement on signale seulement (rien à remplacer).
    if not getattr(sys, "frozen", False):
        return info

    try:
        current_exe = Path(sys.executable)
        new_exe = current_exe.with_name(f".update-{manifest['version']}.exe")
        _download(manifest["url"], new_exe)
        # Vérification d'intégrité forte si le manifeste fournit un sha256.
        expected_hash = (manifest.get("sha256") or "").strip().lower()
        if expected_hash and _sha256(new_exe) != expected_hash:
            new_exe.unlink(missing_ok=True)
            raise IOError("Empreinte sha256 du téléchargement incorrecte")
        # On mémorise le remplacement à effectuer à la fermeture.
        os.environ["OPTIMMO_PENDING_UPDATE"] = str(new_exe)
        info["pending"] = True
        info["_new_exe"] = str(new_exe)
        info["_current_exe"] = str(current_exe)
    except Exception:
        return info  # téléchargement échoué : on n'installe pas, on signale.
    return info


def finalize_pending() -> None:
    """À appeler juste avant de quitter : installe la MAJ téléchargée s'il y en a une."""
    new_exe = os.environ.get("OPTIMMO_PENDING_UPDATE")
    if not (new_exe and Path(new_exe).exists() and getattr(sys, "frozen", False)):
        return
    try:
        # Garde-fou final : ne jamais écraser l'exe courant par un fichier qui
        # n'est pas un exécutable Windows valide (évite d'installer un binaire
        # corrompu → « Failed to load Python DLL »).
        with open(new_exe, "rb") as f:
            if f.read(2) != b"MZ":
                Path(new_exe).unlink(missing_ok=True)
                return
        _stage_replacement(Path(new_exe), Path(sys.executable))
    except Exception:
        pass
