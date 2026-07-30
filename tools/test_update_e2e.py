"""
Test end-to-end du système de mise à jour, contre le VRAI release GitHub.

Simule un poste où la Passerelle est « installée » en version 1.1.1 (exe figé),
puis exécute exactement le code de production (updater.check_and_prepare +
updater.finalize_pending) et vérifie que l'exe est bien téléchargé puis remplacé.

Usage : python tools/test_update_e2e.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# La console Windows par défaut (cp1252) plante sur les emoji ✅/❌ utilisés
# plus bas ; on force l'UTF-8 en sortie pour que le script ne meure jamais
# sur un print, ce qui laisserait le nettoyage (taskkill/rmtree) non exécuté.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
import updater  # noqa: E402


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="optimmo_e2e_"))
    fake_exe = work / "PasserelleOptimmo.exe"
    # « Exe installé » = un fichier bidon ; on prouvera le remplacement par la
    # variation de taille (le vrai exe fait ~76 Mo).
    fake_exe.write_bytes(b"OLD-INSTALLED-1.1.1\n")
    old_size = fake_exe.stat().st_size

    # Simule le mode figé PyInstaller.
    updater.sys.frozen = True
    updater.sys.executable = str(fake_exe)
    updater.__version__ = "1.1.1"  # version « installée »

    cfg = dict(config.DEFAULTS)  # update_url réel (GitHub /latest/)
    print(f"[1] update_url = {cfg['update_url']}")
    print(f"[2] version installée simulée = {updater.__version__}")

    manifest = updater.check_manifest(cfg["update_url"])
    print(f"[3] manifeste distant = {manifest}")
    if not manifest:
        print("ÉCHEC : manifeste introuvable"); return 1
    if not updater.is_newer(manifest["version"]):
        print(f"ABANDON : la version distante {manifest['version']} n'est pas "
              f"plus récente que {updater.__version__} — rien à tester.")
        return 2

    print("[4] téléchargement + préparation…")
    info = updater.check_and_prepare(cfg)
    print(f"    -> info = {info}")
    if not info or not info.get("pending"):
        print("ÉCHEC : aucune mise à jour préparée (pending=False)"); return 1

    staged = work / f".update-{manifest['version']}.exe"
    if not staged.exists():
        print(f"ÉCHEC : fichier téléchargé absent ({staged})"); return 1
    staged_size = staged.stat().st_size
    head = staged.read_bytes()[:2]
    print(f"[5] exe téléchargé : {staged.name} — {staged_size/1e6:.1f} Mo — "
          f"signature PE={head!r}")
    if head != b"MZ":
        print("ÉCHEC : le fichier téléchargé n'est pas un exécutable Windows valide")
        return 1
    if staged_size < 5_000_000:
        print("ÉCHEC : exe téléchargé suspicieusement petit"); return 1

    print("[6] installation à la fermeture (finalize_pending -> script .bat)…")
    updater.finalize_pending()

    # Le .bat attend la fermeture puis fait le move ; on laisse un peu de temps.
    ok = False
    for _ in range(20):
        time.sleep(1)
        if fake_exe.exists() and fake_exe.stat().st_size == staged_size:
            ok = True
            break
    new_size = fake_exe.stat().st_size if fake_exe.exists() else -1
    print(f"    taille exe installé : {old_size} octets -> {new_size} octets")

    # Le script relance l'exe (start) : on tue l'instance lancée par le test.
    subprocess.run(["taskkill", "/IM", "PasserelleOptimmo.exe", "/F"],
                   capture_output=True)

    # Nettoyage best-effort (le bat peut encore tenir le dossier une fraction de s).
    time.sleep(1)
    shutil.rmtree(work, ignore_errors=True)

    if ok:
        print("\n✅ SUCCÈS : l'exe installé a été remplacé par la version "
              f"{manifest['version']} téléchargée depuis GitHub.")
        return 0
    print("\n❌ ÉCHEC : l'exe n'a pas été remplacé dans le délai imparti.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
