"""
Épingle l'icône de la Passerelle dans la zone visible de la barre des tâches
Windows, plutôt que dans le tiroir « icônes cachées ».

Windows retient ce choix par exécutable dans le registre
(HKCU\\Control Panel\\NotifyIconSettings\\<id>\\IsPromoted). Une icône vue pour
la première fois y est ajoutée masquée par défaut ; on force la promotion ici
pour que l'utilisateur n'ait jamais à la sortir manuellement du tiroir, à
chaque poste et après chaque redémarrage de l'explorateur/session.

L'entrée de registre n'existe qu'après que Windows a vu l'icône au moins une
fois : sans effet au tout premier lancement, effectif dès le suivant.
"""
import sys

_KEY = r"Control Panel\NotifyIconSettings"


def promote() -> None:
    """
    Force l'icône de cet exécutable à rester toujours visible. Ne fait rien
    hors Windows ou en mode développement (l'exe non figé, c'est python.exe,
    partagé avec d'autres outils : on ne veut pas le promouvoir par erreur).
    Silencieux en cas d'échec (registre indisponible, etc.) : jamais de crash
    pour un confort d'affichage.
    """
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    import winreg
    exe = sys.executable
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as root:
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub_name, 0,
                                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as sub:
                        path, _ = winreg.QueryValueEx(sub, "ExecutablePath")
                        if path and path.lower() == exe.lower():
                            winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1)
                except OSError:
                    continue
    except OSError:
        pass
