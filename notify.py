"""
Notification Windows (toast natif Win10/11) de fin d'analyse Opticheck.

Doublonne le rapport email : dès que l'analyse d'un DPE est disponible, on
pousse un toast avec la fiabilité et un résumé des écarts. Un clic (corps du
toast ou bouton) ouvre l'Espace Pro sur le réseau technicien.

Dégrade proprement :
  - windows-toasts absent → repli sur la notification pystray si une icône est
    fournie, sinon aucune action ;
  - toute erreur d'affichage est avalée (jamais de crash pour un toast).
"""

_APP_LABEL = "Passerelle Optimmo"
_OPTICHECK_URL = "https://app-espace-pro.optimmo-energies.com/reseau-technicien"

# Classifications d'écarts jugées critiques (aligné sur email_report).
_CRITIQUE_CLASSIFICATIONS = {"false_improvement", "false_deterioration", "non_conforme"}


def _reliability_pct(data: dict) -> int | None:
    """Score de fiabilité Opticheck en pourcentage entier, ou None si absent."""
    score = data.get("score_global_reliability")
    if score is None:
        return None
    try:
        return round(float(score) * 100)
    except (TypeError, ValueError):
        return None


def _count_ecarts(data: dict) -> tuple[int, int]:
    """(nb critiques, nb non-critiques) en ignorant les 'AUCUNE_ANOMALIE'."""
    critiques = non_critiques = 0
    for e in data.get("ecarts", []) or []:
        if e.get("type") == "AUCUNE_ANOMALIE":
            continue
        if e.get("classification") in _CRITIQUE_CLASSIFICATIONS:
            critiques += 1
        else:
            non_critiques += 1
    return critiques, non_critiques


def _reliability_line(pct: int | None) -> str:
    if pct is None:
        return "Fiabilité non disponible"
    dot = "🟢" if pct >= 80 else ("🟠" if pct >= 60 else "🔴")
    return f"{dot} Fiabilité {pct} %"


def _ecarts_line(critiques: int, non_critiques: int) -> str:
    if not critiques and not non_critiques:
        return "✅ Aucun écart détecté"
    bits = []
    if critiques:
        bits.append(f"🔴 {critiques} écart{'s' if critiques > 1 else ''} critique"
                    f"{'s' if critiques > 1 else ''}")
    if non_critiques:
        bits.append(f"🟡 {non_critiques} à surveiller")
    return "   ·   ".join(bits)


def build_texts(summary: dict, data: dict) -> list[str]:
    """Lignes du toast : titre, fiabilité, résumé des écarts."""
    dossier = summary.get("dossier", "—")
    pct = _reliability_pct(data)
    critiques, non_critiques = _count_ecarts(data)
    return [
        f"Analyse Opticheck terminée — {dossier}",
        _reliability_line(pct),
        _ecarts_line(critiques, non_critiques),
    ]


def _fallback(icon, texts: list[str]) -> None:
    """Repli pystray : notification simple, sans clic actionnable."""
    if icon is None:
        return
    try:
        icon.notify(texts[0], "\n".join(texts[1:]))
    except Exception:
        pass


def notify_analysis_complete(summary: dict, data: dict, cfg: dict,
                             icon=None) -> None:
    """
    Affiche le toast de fin d'analyse. `data` est la réponse de
    /recherche_ecarts_dpe (fiabilité + écarts). Ne lève jamais.
    """
    texts = build_texts(summary, data)
    url = (cfg or {}).get("opticheck_link_url") or _OPTICHECK_URL
    try:
        from windows_toasts import (InteractableWindowsToaster, Toast,
                                     ToastButton)
    except Exception:
        _fallback(icon, texts)
        return
    try:
        toaster = InteractableWindowsToaster(_APP_LABEL)
        toast = Toast(
            text_fields=texts,
            launch_action=url,  # clic sur le corps → ouvre l'URL (shell Windows)
            attribution_text="Optimmo Énergies",
            actions=[ToastButton("Ouvrir dans Opticheck", launch=url)],
        )
        toaster.show_toast(toast)
    except Exception:
        _fallback(icon, texts)
