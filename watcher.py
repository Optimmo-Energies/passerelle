"""
Surveille le dossier LICIEL et déclenche un rappel après N secondes
d'inactivité sur les fichiers XML (debounce).
"""
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _Handler(FileSystemEventHandler):
    def __init__(self, on_idle, debounce_seconds: int):
        super().__init__()
        self._on_idle = on_idle
        self._debounce = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_modified(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".xml"):
            self._arm()

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".xml"):
            self._arm()

    def _arm(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._on_idle)
            self._timer.daemon = True
            self._timer.start()


def start(liciel_root: str, on_idle, debounce_seconds: int = 120) -> Observer | None:
    """
    Lance l'observation du dossier LICIEL en arrière-plan.
    on_idle() est appelé après debounce_seconds secondes sans modification XML.

    Si le dossier LICIEL est absent (chemin mal configuré ou LICIEL non
    installé), renvoie None sans planter : l'appli démarre quand même et
    l'envoi manuel reste possible.
    """
    if not Path(liciel_root).is_dir():
        return None
    handler = _Handler(on_idle, debounce_seconds)
    obs = Observer()
    obs.schedule(handler, str(liciel_root), recursive=True)
    obs.daemon = True
    obs.start()
    return obs
