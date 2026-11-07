"""
Mail récapitulatif envoyé au diagnostiqueur après chaque transmission.

Un envoi = un mail : qu'il transmette un dossier (envoi rapide) ou plusieurs
(sélection), le diagnostiqueur reçoit UN mail listant chaque dossier, son
analyse Opticheck (tous les écarts) et les éventuels échecs.

Fonctionnement :
  - l'analyse Opticheck de chaque DPE revient dans la réponse de l'upload
    (`send.Transmission.reponse["results"][i]["analysis"]`) ;
  - la Passerelle les regroupe (`Lot`) puis appelle UNE fois
    `POST <dpe_ingest>/dpe_en_cours/recap_email` avec le jeton Espace Pro ;
  - c'est dpe_ingest qui envoie le mail, à l'adresse du compte connecté :
    aucun identifiant SMTP n'est embarqué dans l'exe.

Tout se passe en arrière-plan et ne lève jamais : un mail raté ne doit pas
faire échouer ni ralentir une transmission. Un toast Windows reprend le
résultat de l'analyse dès la fin du lot.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

LOG_FILE = Path.home() / ".optimo_bridge" / "recap.log"

_RESUME_KEYS = ("numero_ademe", "surface", "annee", "methode", "cout_annuel",
                "adresse", "classe_energie", "consommation", "type_batiment")


def _log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def analyses(result) -> list[dict]:
    """Analyses Opticheck contenues dans la réponse d'upload (peut être vide)."""
    rep = getattr(result, "reponse", None)
    if not isinstance(rep, dict):
        return []
    out = []
    for r in rep.get("results") or []:
        a = r.get("analysis") if isinstance(r, dict) else None
        if isinstance(a, dict):
            # Champs volumineux ou internes inutiles au mail.
            out.append({k: v for k, v in a.items()
                        if k not in ("ban_x", "ban_y", "origin")})
    return out


def _resume(summary: dict) -> dict:
    return {k: summary.get(k) for k in _RESUME_KEYS
            if summary.get(k) not in (None, "", "—")}


class Lot:
    """Dossiers d'un même envoi, à récapituler dans un seul mail."""

    def __init__(self):
        self.items: list[dict] = []
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.items)

    def transmis(self, summary: dict, logiciel: str, result,
                 remarques: list[str] | None = None) -> None:
        with self._lock:
            self.items.append({
                "dossier": str(summary.get("dossier") or "—")[:300],
                "logiciel": logiciel,
                "adresse": (str(summary.get("adresse"))[:500]
                            if summary.get("adresse") else None),
                "statut": "transmis",
                "resume": _resume(summary),
                "analyses": analyses(result)[:10],
                "remarques": [r for r in (remarques or []) if r][:20],
            })

    def echec(self, dossier: str, logiciel: str, erreur) -> None:
        with self._lock:
            self.items.append({
                "dossier": str(dossier or "—")[:300],
                "logiciel": logiciel,
                "statut": "erreur",
                "erreur": str(erreur)[:2000],
            })


def recap_url(cfg: dict) -> str:
    """URL de l'endpoint récapitulatif, déduite de celle de l'upload."""
    if cfg.get("recap_email_url"):
        return cfg["recap_email_url"]
    api = (cfg.get("api_url") or "").rstrip("/")
    if api.endswith("/upload"):
        return api[: -len("/upload")] + "/recap_email"
    return api + "/recap_email"


def _post(cfg: dict, items: list[dict]) -> bool:
    import auth
    url = recap_url(cfg)
    body = {"dossiers": items}
    for attempt in range(3):
        token = auth.valid_access_token(cfg)
        if not token:
            _log("récap non envoyé : pas de session Espace Pro")
            return False
        try:
            resp = requests.post(url, json=body, timeout=60,
                                 headers={"Authorization": f"Bearer {token}"})
        except requests.RequestException as e:
            _log(f"récap : réseau ({e}), tentative {attempt + 1}/3")
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 403 and "Access Token Is Expired" in resp.text:
            auth.refresh(cfg)
            continue
        if resp.ok:
            _log(f"récap envoyé ({len(items)} dossier(s))")
            return True
        _log(f"récap : HTTP {resp.status_code} {resp.text[:300]}")
        if resp.status_code < 500:
            return False  # erreur définitive (404 = endpoint pas encore déployé…)
        time.sleep(5 * (attempt + 1))
    return False


def _toast(lot: Lot, cfg: dict, icon) -> None:
    """Toast de fin d'analyse, à partir des analyses reçues."""
    import notify
    analysed = [(it, a) for it in lot.items for a in it.get("analyses") or []]
    if not analysed:
        return
    if len(analysed) == 1:
        it, a = analysed[0]
        notify.notify_analysis_complete({"dossier": it["dossier"]}, a, cfg, icon)
        return
    crit = autres = 0
    for _, a in analysed:
        c, o = notify._count_ecarts(a)
        crit += c
        autres += o
    texts = [f"Analyse Opticheck terminée — {len(analysed)} DPE",
             notify._ecarts_line(crit, autres),
             "Le détail vous est envoyé par mail."]
    try:
        icon.notify(texts[0], "\n".join(texts[1:])) if icon else None
    except Exception:
        pass


def _run(lot: Lot, cfg: dict, icon) -> None:
    try:
        _toast(lot, cfg, icon)
    except Exception:
        pass
    try:
        if cfg.get("demo_mode", True):
            # Mode démo : rien ne part ; on dépose la charge utile pour contrôle.
            out = Path(cfg.get("output_dir") or ".")
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"recap_{datetime.now():%Y%m%d_%H%M%S}.json"
            f.write_text(json.dumps({"dossiers": lot.items}, ensure_ascii=False,
                                    indent=2, default=str), encoding="utf-8")
            return
        _post(cfg, lot.items)
    except Exception as e:
        _log(f"récap : erreur inattendue {e!r}")


def dispatch(lot: Lot, cfg: dict, icon=None, wait: bool = False) -> None:
    """Envoie le récapitulatif du lot (en arrière-plan). Ne lève jamais."""
    if not lot.items or not cfg.get("recap_email", True):
        return
    t = threading.Thread(target=_run, args=(lot, cfg, icon), daemon=True)
    t.start()
    if wait:
        t.join(120)
