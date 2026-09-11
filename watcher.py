"""
Détection d'un DPE en cours de rédaction, pour rappeler à l'utilisateur de le
transmettre avant validation.

Deux mécanismes, selon le logiciel :

- **LICIEL** écrit ses dossiers sous forme de fichiers XML : on surveille
  l'arborescence et on déclenche le rappel après N secondes sans modification
  (debounce).
- **Analys'immo** écrit en base de données : il n'y a aucun fichier à guetter.
  On sonde donc périodiquement l'horodatage des missions DPE, et on déclenche
  le rappel quand une modification a été observée puis s'est stabilisée — même
  principe de debounce, appliqué à un signal en base.
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


class AdnPoller:
    """
    Sondage périodique des missions DPE d'Analys'immo.

    `on_idle()` est appelé une fois par DPE dès qu'une modification a été
    constatée puis qu'un cycle de sondage s'est écoulé sans nouvelle
    modification : le diagnostiqueur a arrêté de saisir, c'est le bon moment
    pour lui proposer la transmission.

    Les DPE déjà télétransmis à l'ADEME ne déclenchent rien : il est trop tard
    pour les faire analyser avant validation.
    """

    def __init__(self, cfg: dict, on_idle, poll_seconds: int = 180):
        self._cfg = cfg
        self._on_idle = on_idle
        self._poll = max(30, int(poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Dernière signature observée et son état : (signature, deja_signalee)
        self._seen: tuple[str, bool] = ("", True)

    # ── cycle de vie ────────────────────────────────────────────────────────
    def start(self) -> "AdnPoller":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ── boucle ──────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # Base momentanément injoignable (ADN fermé, poste en veille,
                # serveur redémarré) : on réessaiera au prochain cycle.
                pass
            self._stop.wait(self._poll)

    def _tick(self) -> None:
        signature = self._signature()
        if not signature:
            return
        previous, already = self._seen
        if signature != previous:
            # Modification en cours : on attend le prochain cycle pour voir si
            # la saisie s'est arrêtée.
            self._seen = (signature, False)
            return
        if not already:
            self._seen = (signature, True)
            self._on_idle()

    def _signature(self) -> str:
        """
        Empreinte de l'état des DPE non transmis : identifiant de la mission la
        plus récemment modifiée et son horodatage. Change à chaque
        enregistrement dans Analys'immo.
        """
        import adn
        src = adn.open_source(self._cfg)
        dossier = adn.find_latest_dossier(src)
        if dossier is None:
            return ""
        missions = adn.get_dpe_missions(src, dossier["idDossier"])
        if not missions:
            return ""
        mission = missions[0]
        summary = adn.parse_dpe_summary(src, dossier, mission)
        if summary.get("adn", {}).get("transmis_ademe"):
            return ""
        return f"{mission['idMission']}@{mission.get('dateMaj') or ''}" \
               f"|{dossier.get('dateMaj') or ''}"


def start_adn(cfg: dict, on_idle, poll_seconds: int = 180) -> AdnPoller | None:
    """
    Lance le sondage Analys'immo en arrière-plan. Renvoie None si Analys'immo
    n'est pas installé sur le poste, pour que l'appelant reste silencieux.
    """
    import diag_setup
    if not diag_setup.adn_present(cfg):
        return None
    return AdnPoller(cfg, on_idle, poll_seconds).start()
