import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

import auth
import config
import diag_setup
import dialog
import email_report
import icon_gen
import liciel
import send
import startup
import tray_pin
import updater
import watcher
from version import __version__

_alert = {"active": False}
# État de la surveillance (LICIEL sur fichiers, Analys'immo par sondage en
# base), conservé au niveau module pour permettre son redémarrage après une
# (re)configuration du logiciel de diagnostic.
_watch = {"obs": None, "adn": None, "on_idle": None,
          "debounce": 120, "poll": 180}


def _apply_source(cfg: dict, picked: tuple[str, str]) -> None:
    """Reporte le logiciel détecté (source, chemin) dans la configuration."""
    source, value = picked
    if source == "liciel":
        cfg["liciel_root"] = value
    elif source == "adn":
        # `classify_dir` renvoie normalement le dossier d'installation ; un
        # chemin de fichier ne peut être qu'une base .sdf d'évaluation.
        if Path(value).is_file():
            cfg["analysimo_sdf"] = value
        else:
            cfg["adn_root"] = value


def _stop_watch() -> None:
    """
    Arrête les deux détecteurs et attend la fin du sondage Analys'immo.

    L'attente n'est pas cosmétique : le sondage dialogue avec .NET via
    pythonnet, et un thread interrompu en pleine requête au moment où
    l'interpréteur se referme fait remonter une NullReferenceException à
    l'utilisateur au lieu d'une fermeture propre.
    """
    for key in ("obs", "adn"):
        if _watch[key] is not None:
            try:
                _watch[key].stop()
            except Exception:
                pass
    if _watch["adn"] is not None:
        try:
            _watch["adn"].join(timeout=10)
        except Exception:
            pass
    _watch["obs"] = None
    _watch["adn"] = None


def _restart_watch(cfg: dict) -> None:
    """
    (Re)démarre la détection de DPE en cours, pour chaque logiciel présent :
    surveillance de fichiers pour LICIEL, sondage en base pour Analys'immo.
    Reste silencieux pour un logiciel absent — l'envoi manuel reste disponible.
    """
    _stop_watch()
    if _watch["on_idle"] is None:
        return
    _watch["obs"] = watcher.start(
        cfg["liciel_root"], _watch["on_idle"], _watch["debounce"]
    )
    _watch["adn"] = watcher.start_adn(cfg, _watch["on_idle"], _watch["poll"])


def _make_icon(alert: bool = False) -> Image.Image:
    if alert:
        return icon_gen.make_tray_icon_alert()
    ico_path = Path(__file__).parent / "icon_tray.png"
    if ico_path.exists():
        return Image.open(ico_path).convert("RGB")
    return icon_gen.make_tray_icon()


def _liciel_ready(cfg: dict) -> bool:
    return diag_setup.liciel_present(cfg.get("liciel_root", ""))


def _adn_ready(cfg: dict) -> bool:
    """Analys'immo est installé et sa base est atteignable."""
    return diag_setup.adn_present(cfg)


def _get_dossier_label(cfg: dict) -> str:
    dossier = liciel.find_latest_dossier(cfg["liciel_root"])
    if dossier:
        return f"Dossier actif : {dossier.name}"
    # Racine configurée mais vide : le cas type est un LICIEL qui enregistre
    # ailleurs que dans le dossier par défaut.
    return "Aucun dossier trouvé — vérifiez le dossier LICIEL"


def _adn_dossier_label(cfg: dict) -> str:
    """
    Dernier DPE Analys'immo, pour la ligne d'état. Toute erreur de lecture est
    résumée sur cette ligne plutôt que masquée : c'est souvent le premier
    indice qu'a l'utilisateur qu'une base n'est pas joignable.
    """
    try:
        import adn
        src = adn.open_source(cfg)
        dossier = adn.find_latest_dossier(src)
        if dossier is None:
            return "Analys'immo — aucun dossier avec mission DPE"
        return f"DPE Analys'immo actif : {dossier.get('reference') or '?'}"
    except Exception as e:
        return f"Analys'immo illisible — {str(e).splitlines()[0][:60]}"


def _status_label(cfg: dict) -> str:
    """Ligne d'état en tête de menu, adaptée au logiciel de diagnostic présent."""
    if _liciel_ready(cfg):
        return _get_dossier_label(cfg)
    if _adn_ready(cfg):
        return _adn_dossier_label(cfg)
    return "Aucun logiciel de diagnostic configuré"


def _set_alert(icon: pystray.Icon, active: bool) -> None:
    _alert["active"] = active
    icon.icon = _make_icon(alert=active)
    icon.title = (
        "Passerelle Optimmo — Dossier en attente de transmission"
        if active else
        "Passerelle Optimmo"
    )


def _send_one(dossier: Path, cfg: dict, icon: pystray.Icon | None = None) -> str:
    """Transmet un dossier LICIEL et programme son rapport. Renvoie un message."""
    summary = liciel.parse_dpe_summary(dossier)
    xml_files = liciel.get_xml_files(dossier)
    result = send.send_dpe(xml_files, summary, cfg, dossier=dossier)
    email_report.schedule_report(summary, cfg, icon=icon)
    return result


def _send_one_adn(src, dossier_row: dict, mission_row: dict, cfg: dict,
                  icon: pystray.Icon | None = None) -> str:
    """
    Transmet un DPE Analys'immo et programme son rapport. Le DPE est extrait
    des bases ADN à l'instant de l'envoi : c'est toujours l'état enregistré
    le plus récent qui part, sans qu'il soit besoin de le télétransmettre à
    l'ADEME au préalable.
    """
    import adn
    summary = adn.parse_dpe_summary(src, dossier_row, mission_row)
    payload = adn.read_dpe(src, dossier_row, mission_row)
    # XML ADEME au modèle officiel, comme pour LICIEL : c'est lui qu'Opticheck
    # sait déjà lire. La saisie brute reste jointe en complément.
    ademe, rapport = send._try_ademe_adn(src, dossier_row, mission_row, cfg)
    result = send.send_adn_dpe(summary, payload, cfg, ademe, rapport)
    email_report.schedule_report(summary, cfg, icon=icon)
    return result


def _propose_reconnect(icon: pystray.Icon, cfg: dict, message: str) -> bool:
    """Affiche le dialogue de reconnexion. Renvoie True si l'utilisateur s'est
    reconnecté ; rafraîchit le menu et notifie dans tous les cas."""
    def _reconnect() -> bool:
        ok = auth.login(cfg)
        if ok:
            auth.current_user(cfg)  # peuple le cache pour le menu
        return ok

    ok = dialog.show_reauth_dialog(message, _reconnect)
    icon.menu = _build_menu(icon, cfg)
    if ok:
        user = auth.cached_user() or {}
        email = user.get("email_address")
        icon.notify("Passerelle Optimmo",
                    f"Reconnecté{f' : {email}' if email else ''}.")
    return ok


def _ensure_authenticated(icon: pystray.Icon, cfg: dict) -> bool:
    """
    Garantit une session Espace Pro exploitable avant transmission :
      - auth non requise → True ;
      - jeton valide (rafraîchi silencieusement si expiré) → True ;
      - sinon → propose une reconnexion et renvoie True si elle réussit.
    """
    if not cfg.get("require_auth"):
        return True
    had_session = auth.is_authenticated()
    if auth.valid_access_token(cfg):
        return True
    message = (
        "Votre session Espace Pro a expiré.\n"
        "Reconnectez-vous pour transmettre vos DPE."
        if had_session else
        "Connectez-vous à l'Espace Pro pour transmettre vos DPE."
    )
    return _propose_reconnect(icon, cfg, message)


def _on_send_adn(icon: pystray.Icon, cfg: dict) -> None:
    """Envoi rapide du dernier DPE Analys'immo enregistré."""
    _set_alert(icon, False)
    if not _ensure_authenticated(icon, cfg):
        return

    import adn
    try:
        src = adn.open_source(cfg)
        dossier = adn.find_latest_dossier(src)
    except Exception as e:
        dialog.show_message(f"Lecture d'Analys'immo impossible :\n\n{e}")
        return

    if dossier is None:
        dialog.show_message(
            "Aucun dossier Analys'immo ne porte de mission DPE.\n\n"
            "Créez la mission dans Analys'immo, puis relancez l'envoi.")
        return

    missions = adn.get_dpe_missions(src, dossier["idDossier"])
    if not missions:
        dialog.show_message(
            f"Le dossier {dossier.get('reference')} n'a pas de mission DPE.\n"
            "Utilisez « Choisir les DPE Analys'immo à envoyer… ».")
        return

    mission = missions[0]
    summary = adn.parse_dpe_summary(src, dossier, mission)
    state = {"reauth": False}

    def do_send() -> str:
        try:
            return _send_one_adn(src, dossier, mission, cfg, icon)
        except auth.ReauthRequired:
            state["reauth"] = True
            return ("Session Espace Pro expirée pendant l'envoi.\n"
                    "Une reconnexion va vous être proposée.")
        except Exception as e:
            return f"Erreur lors de l'envoi :\n{e}"

    # Une source en base n'a pas de fichiers à dénombrer : on annonce la
    # mission elle-même comme unique pièce transmise.
    dialog.show_confirmation_dialog(summary, 1, do_send)

    if state["reauth"] and _propose_reconnect(
        icon, cfg, "Votre session Espace Pro a expiré.\n"
                   "Reconnectez-vous pour transmettre ce DPE."
    ):
        try:
            dialog.show_message(_send_one_adn(src, dossier, mission, cfg, icon))
        except Exception as e:
            dialog.show_message(f"Erreur lors de l'envoi :\n{e}")


def _on_select_adn(icon: pystray.Icon, cfg: dict) -> None:
    """Ouvre la liste des DPE Analys'immo récents pour en choisir un ou plusieurs."""
    _set_alert(icon, False)
    if not _ensure_authenticated(icon, cfg):
        return

    import adn
    try:
        src = adn.open_source(cfg)
        enriched = adn.dossiers_avec_resume(
            src, limit=cfg.get("dossier_list_limit", 30))
    except Exception as e:
        dialog.show_message(f"Lecture d'Analys'immo impossible :\n\n{e}")
        return

    if not enriched:
        dialog.show_message(
            "Aucun dossier Analys'immo ne porte de mission DPE.")
        return

    state = {"reauth": False}

    def on_send(selection: list[dict]) -> str:
        ok, errors = [], []
        for item in selection:
            try:
                _send_one_adn(src, item["_dossier_row"], item["_mission_row"],
                              cfg, icon)
                ok.append(item["dossier"])
            except auth.ReauthRequired:
                # Session morte : inutile de continuer le lot.
                state["reauth"] = True
                break
            except Exception as e:
                errors.append(f"{item['dossier']} : {e}")
        lines = [f"{len(ok)} DPE transmis avec succès."]
        if ok:
            lines.append("• " + "\n• ".join(ok))
        if errors:
            lines.append(f"\nErreur(s) ({len(errors)}) :")
            lines.append("• " + "\n• ".join(errors))
        if state["reauth"]:
            lines.append("\n⚠ Session Espace Pro expirée : une reconnexion va "
                         "vous être proposée. Relancez ensuite l'envoi des "
                         "DPE restants.")
        return "\n".join(lines)

    dialog.show_dossier_selection_dialog(enriched, on_send)

    if state["reauth"]:
        _propose_reconnect(
            icon, cfg, "Votre session Espace Pro a expiré.\n"
                       "Reconnectez-vous, puis relancez l'envoi des DPE restants.")


def _on_send(icon: pystray.Icon, cfg: dict) -> None:
    """Envoi rapide du dernier dossier (avec mission DPE)."""
    _set_alert(icon, False)
    if not _liciel_ready(cfg):
        # L'entrée n'est proposée qu'en présence de LICIEL ; on bascule quand
        # même vers Analys'immo si c'est le seul logiciel du poste.
        if _adn_ready(cfg):
            _on_send_adn(icon, cfg)
        else:
            dialog.show_message("Aucun logiciel de diagnostic configuré.")
        return
    if not _ensure_authenticated(icon, cfg):
        return

    dossier = liciel.find_latest_dossier(cfg["liciel_root"])
    if dossier is None:
        dialog.show_confirmation_dialog(
            {"dossier": "Introuvable"}, 0,
            lambda: ("Erreur : aucun dossier LICIEL détecté dans\n"
                     f"{cfg.get('liciel_root', '')}\n\n"
                     "Si LICIEL enregistre ailleurs, indiquez le bon dossier "
                     "via « Dossier d'enregistrement LICIEL… ».")
        )
        return

    if not liciel.has_dpe_mission(dossier):
        dialog.show_confirmation_dialog(
            {"dossier": dossier.name}, 0,
            lambda: ("Aucune mission DPE associée au dernier dossier.\n"
                     "Rien à transmettre. Utilisez « Choisir les dossiers… ».")
        )
        return

    summary = liciel.parse_dpe_summary(dossier)
    xml_files = liciel.get_xml_files(dossier)

    state = {"reauth": False}

    def do_send() -> str:
        try:
            return _send_one(dossier, cfg, icon)
        except auth.ReauthRequired:
            state["reauth"] = True
            return ("Session Espace Pro expirée pendant l'envoi.\n"
                    "Une reconnexion va vous être proposée.")
        except Exception as e:
            return f"Erreur lors de l'envoi :\n{e}"

    dialog.show_confirmation_dialog(summary, len(xml_files), do_send)

    # Session expirée en cours d'envoi → proposer la reconnexion puis rejouer.
    if state["reauth"] and _propose_reconnect(
        icon, cfg, "Votre session Espace Pro a expiré.\n"
                   "Reconnectez-vous pour transmettre ce dossier."
    ):
        try:
            dialog.show_message(_send_one(dossier, cfg, icon))
        except Exception as e:
            dialog.show_message(f"Erreur lors de l'envoi :\n{e}")


def _on_select(icon: pystray.Icon, cfg: dict) -> None:
    """Ouvre la liste des dossiers récents pour en choisir un ou plusieurs."""
    _set_alert(icon, False)
    if not _liciel_ready(cfg):
        if _adn_ready(cfg):
            _on_select_adn(icon, cfg)
        else:
            dialog.show_message("Aucun logiciel de diagnostic configuré.")
        return
    if not _ensure_authenticated(icon, cfg):
        return
    limit = cfg.get("dossier_list_limit", 30)
    dossiers = liciel.list_dossiers(cfg["liciel_root"], limit=limit)
    if not dossiers:
        dialog.show_confirmation_dialog(
            {"dossier": "Introuvable"}, 0,
            lambda: ("Erreur : aucun dossier LICIEL détecté dans\n"
                     f"{cfg.get('liciel_root', '')}\n\n"
                     "Si LICIEL enregistre ailleurs, indiquez le bon dossier "
                     "via « Dossier d'enregistrement LICIEL… ».")
        )
        return

    enriched = []
    for d in dossiers:
        summary = liciel.parse_dpe_summary(d)
        summary["path"] = d
        enriched.append(summary)

    state = {"reauth": False}

    def on_send(selection: list[dict]) -> str:
        ok, errors = [], []
        for item in selection:
            try:
                _send_one(item["path"], cfg, icon)
                ok.append(item["dossier"])
            except auth.ReauthRequired:
                # Session morte : inutile de continuer le lot.
                state["reauth"] = True
                break
            except Exception as e:
                errors.append(f"{item['dossier']} : {e}")
        lines = [f"{len(ok)} dossier(s) transmis avec succès."]
        if ok:
            lines.append("• " + "\n• ".join(ok))
        if errors:
            lines.append(f"\nErreur(s) ({len(errors)}) :")
            lines.append("• " + "\n• ".join(errors))
        if state["reauth"]:
            lines.append("\n⚠ Session Espace Pro expirée : une reconnexion va "
                         "vous être proposée. Relancez ensuite l'envoi des "
                         "dossiers restants.")
        return "\n".join(lines)

    dialog.show_dossier_selection_dialog(enriched, on_send)

    # Session expirée en cours de lot → proposer la reconnexion.
    if state["reauth"]:
        _propose_reconnect(
            icon, cfg, "Votre session Espace Pro a expiré.\n"
                       "Reconnectez-vous, puis relancez l'envoi des dossiers restants."
        )


def _inspect_liciel_root(path: str) -> tuple[bool, str]:
    """
    Valide un dossier LICIEL saisi par l'utilisateur et décrit ce qu'il
    contient. Un dossier existant mais vide est accepté (avertissement) :
    l'important est qu'il corresponde à l'emplacement choisi dans LICIEL.
    """
    raw = (path or "").strip().strip('"')
    if not raw:
        return False, "Indiquez un dossier."
    p = Path(raw)
    if not p.is_dir():
        return False, "Ce dossier n'existe pas."
    info = liciel.scan_root(str(p))
    if not info["total"]:
        return True, ("Aucun dossier LICIEL détecté ici pour l'instant. "
                      "Vérifiez qu'il s'agit bien du dossier configuré dans "
                      "LICIEL (le chemin sera enregistré malgré tout).")
    return True, (f"{info['total']} dossier(s) détecté(s), "
                  f"{info['avec_dpe']} avec une mission DPE — "
                  f"dernier : {info['dernier']}.")


def _on_configure_liciel_folder(icon: pystray.Icon, cfg: dict) -> None:
    """
    Laisse l'utilisateur choisir le dossier d'enregistrement LICIEL. LICIEL
    permet de changer cet emplacement : la racine par défaut peut exister sans
    contenir les dossiers réellement utilisés.
    """
    chosen = dialog.show_liciel_folder_dialog(
        cfg.get("liciel_root", ""), _inspect_liciel_root)
    if not chosen:
        return
    cfg["liciel_root"] = diag_setup.normalize_liciel_root(chosen)
    config.save(cfg)
    _restart_watch(cfg)
    icon.menu = _build_menu(icon, cfg)
    icon.notify("Passerelle Optimmo",
                f"Dossier LICIEL : {cfg['liciel_root']}")


def _on_configure_diag(icon: pystray.Icon, cfg: dict) -> None:
    """Laisse l'utilisateur (re)sélectionner le dossier de son logiciel de diag."""
    picked = dialog.show_diag_setup_dialog(
        diag_setup.classify_dir,
        diag_setup.SOURCE_LABELS,
        heading="Logiciel de diagnostic",
        body=("Sélectionnez le dossier racine de votre logiciel de diagnostic "
              "(LICIEL Diagnostics ou ADN Evaluation). La passerelle s'y "
              "connectera pour transmettre vos DPE."),
    )
    if not picked:
        return
    _apply_source(cfg, picked)
    config.save(cfg)
    _restart_watch(cfg)
    icon.menu = _build_menu(icon, cfg)
    label = diag_setup.SOURCE_LABELS.get(picked[0], picked[0])
    icon.notify("Passerelle Optimmo", f"{label} configuré.")


def _toggle_boot(icon: pystray.Icon, cfg: dict) -> None:
    cfg["start_at_boot"] = not cfg.get("start_at_boot", True)
    config.save(cfg)
    startup.ensure(cfg["start_at_boot"])
    icon.menu = _build_menu(icon, cfg)


def _on_login(icon: pystray.Icon, cfg: dict) -> None:
    ok = auth.login(cfg)
    if ok:
        user = auth.current_user(cfg)  # peuple le cache pour le menu
        name = (user or {}).get("email_address", "")
        icon.notify("Passerelle Optimmo",
                    f"Connecté{f' : {name}' if name else ''}.")
    else:
        icon.notify("Passerelle Optimmo", "Échec de la connexion.")
    icon.menu = _build_menu(icon, cfg)


def _on_logout(icon: pystray.Icon, cfg: dict) -> None:
    auth.logout()
    icon.notify("Passerelle Optimmo", "Déconnecté.")
    icon.menu = _build_menu(icon, cfg)


def _on_technician_page(cfg: dict) -> None:
    """Ouvre la page technicien de l'utilisateur sur l'Espace Pro."""
    webbrowser.open(cfg.get("opticheck_link_url") or config.DEFAULTS["opticheck_link_url"])


def _auth_menu_items(cfg: dict) -> list:
    """Entrées de menu d'authentification (vides si require_auth désactivé)."""
    if not cfg.get("require_auth"):
        return []
    if auth.is_authenticated():
        user = auth.cached_user()  # pas d'appel réseau ici
        email = (user or {}).get("email_address")
        label = f"Connecté : {email}" if email else "Connecté"
        return [
            pystray.MenuItem(label, None, enabled=False),
            pystray.MenuItem(
                "Ma page technicien (Espace Pro)…",
                lambda icon, item: _on_technician_page(cfg),
            ),
            pystray.MenuItem(
                "Se déconnecter",
                lambda icon, item: _on_logout(icon, cfg),
            ),
            pystray.Menu.SEPARATOR,
        ]
    return [
        pystray.MenuItem("🔒  Non connecté", None, enabled=False),
        pystray.MenuItem(
            "Se connecter à l'Espace Pro…",
            lambda icon, item: threading.Thread(
                target=_on_login, args=(icon, cfg), daemon=True
            ).start(),
            default=True,  # action mise en avant (double-clic sur l'icône)
        ),
        pystray.Menu.SEPARATOR,
    ]


def _is_logged_in(cfg: dict) -> bool:
    """Autorisé à transmettre : soit l'auth n'est pas requise, soit on a un jeton."""
    return not cfg.get("require_auth") or auth.is_authenticated()


def _source_menu_items(cfg: dict) -> list:
    """
    Actions de transmission, une paire par logiciel de diagnostic présent.
    Les deux peuvent cohabiter sur un même poste : le libellé précise alors
    l'origine du DPE pour lever l'ambiguïté.
    """
    mode_label = "[DÉMO] " if cfg.get("demo_mode") else ""
    liciel_ok, adn_ok = _liciel_ready(cfg), _adn_ready(cfg)
    items: list = []

    def entry(label, target):
        return pystray.MenuItem(
            label,
            lambda icon, item: threading.Thread(
                target=target, args=(icon, cfg), daemon=True).start(),
            enabled=lambda item: _is_logged_in(cfg),
        )

    if liciel_ok:
        suffixe = " (LICIEL)" if adn_ok else ""
        items += [
            entry(f"{mode_label}Envoyer le dernier dossier{suffixe}", _on_send),
            entry(f"{mode_label}Choisir les dossiers à envoyer…{suffixe}",
                  _on_select),
        ]
    if adn_ok:
        suffixe = " (Analys'immo)" if liciel_ok else ""
        items += [
            entry(f"{mode_label}Envoyer le dernier DPE{suffixe}", _on_send_adn),
            entry(f"{mode_label}Choisir les DPE à envoyer…{suffixe}",
                  _on_select_adn),
        ]
    return items


def _build_menu(icon: pystray.Icon, cfg: dict) -> pystray.Menu:
    items = [
        pystray.MenuItem(
            lambda item: (
                "⚠  Dossier en cours — pensez à transmettre !"
                if _alert["active"] else
                _status_label(cfg)
            ),
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        *_auth_menu_items(cfg),
        *_source_menu_items(cfg),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Configurer le logiciel de diagnostic…",
            lambda icon, item: threading.Thread(
                target=_on_configure_diag, args=(icon, cfg), daemon=True
            ).start(),
        ),
        pystray.MenuItem(
            "Dossier d'enregistrement LICIEL…",
            lambda icon, item: threading.Thread(
                target=_on_configure_liciel_folder, args=(icon, cfg), daemon=True
            ).start(),
        ),
        pystray.MenuItem(f"Version {__version__}", None, enabled=False),
        pystray.MenuItem(
            "Lancer au démarrage de Windows",
            lambda icon, item: _toggle_boot(icon, cfg),
            checked=lambda item: cfg.get("start_at_boot", True),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", lambda icon, item: icon.stop()),
    ]
    return pystray.Menu(*items)


def _post_start(icon: pystray.Icon, cfg: dict) -> None:
    """Tâches après affichage de l'icône : démarrage auto + MAJ + état auth."""
    startup.ensure(cfg.get("start_at_boot", True))
    tray_pin.promote()

    if cfg.get("require_auth"):
        if auth.is_authenticated():
            auth.current_user(cfg)  # peuple le cache (réseau, en arrière-plan)
            icon.menu = _build_menu(icon, cfg)
        else:
            icon.notify(
                "Passerelle Optimmo",
                "Connectez-vous à l'Espace Pro pour transmettre vos DPE "
                "(menu de l'icône → « Se connecter… »).",
            )

    # Racine LICIEL configurée mais sans aucun dossier : le plus souvent, LICIEL
    # enregistre à un autre emplacement que celui par défaut.
    if _liciel_ready(cfg) and not liciel.has_dossiers(cfg["liciel_root"]):
        icon.notify(
            "Passerelle Optimmo",
            "Aucun dossier DPE trouvé dans le dossier LICIEL configuré. "
            "Indiquez le dossier où LICIEL enregistre vos dossiers "
            "(menu de l'icône → « Dossier d'enregistrement LICIEL… »).",
        )

    update = updater.check_and_prepare(cfg)
    if update:
        if update.get("pending"):
            icon.notify(
                "Passerelle Optimmo",
                f"Mise à jour {update['version']} téléchargée — "
                "elle s'installera à la fermeture de l'application.",
            )
        else:
            icon.notify(
                "Passerelle Optimmo",
                f"Une nouvelle version ({update['version']}) est disponible.",
            )


def run() -> None:
    cfg = config.load()

    # Aucun logiciel de diagnostic (LICIEL ou ADN Evaluation) détecté →
    # on l'explique et on propose de sélectionner son dossier avant de démarrer.
    if not diag_setup.any_source_present(cfg):
        picked = dialog.show_diag_setup_dialog(
            diag_setup.classify_dir, diag_setup.SOURCE_LABELS
        )
        if picked:
            _apply_source(cfg, picked)
            config.save(cfg)

    icon = pystray.Icon(
        name="optimmo_passerelle",
        icon=_make_icon(),
        title=f"Passerelle Optimmo v{__version__}",
    )
    icon.menu = _build_menu(icon, cfg)

    def on_dossier_idle():
        _set_alert(icon, True)
        icon.notify(
            "Passerelle Optimmo",
            "Un dossier DPE est en cours — pensez à le transmettre avant validation.",
        )

    _watch["on_idle"] = on_dossier_idle
    _watch["debounce"] = cfg.get("reminder_debounce_seconds", 120)
    _watch["poll"] = cfg.get("adn_poll_seconds", 180)
    _restart_watch(cfg)

    def setup(icon_):
        icon_.visible = True
        threading.Thread(target=_post_start, args=(icon_, cfg), daemon=True).start()

    try:
        icon.run(setup=setup)
    finally:
        _stop_watch()
        updater.finalize_pending()
