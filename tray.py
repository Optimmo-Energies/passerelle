import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

import auth
import config
import diag_setup
import dialog
import icon_gen
import liciel
import recap
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


_adn_label = {"value": None, "at": 0.0, "busy": False, "icon": None}


def _adn_dossier_label(cfg: dict) -> str:
    """
    Ligne d'état Analys'immo, servie depuis un cache rafraîchi en arrière-plan.
    La lecture en base peut prendre plusieurs secondes (instance LocalDB qui
    démarre, serveur réseau lent) : elle ne doit jamais figer le menu ni
    retarder l'apparition de l'icône.
    """
    import time
    now = time.monotonic()
    if not _adn_label["busy"] and (_adn_label["value"] is None
                                   or now - _adn_label["at"] > 60):
        _adn_label["busy"] = True

        def refresh():
            try:
                _adn_label["value"] = _adn_dossier_label_now(cfg)
                _adn_label["at"] = time.monotonic()
            finally:
                _adn_label["busy"] = False
            icon = _adn_label.get("icon")
            if icon is not None:
                try:
                    icon.update_menu()
                except Exception:
                    pass

        threading.Thread(target=refresh, daemon=True).start()
    return _adn_label["value"] or "Analys'immo — lecture en cours…"


def _adn_dossier_label_now(cfg: dict) -> str:
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


def _send_one(dossier: Path, cfg: dict, icon: pystray.Icon | None = None,
              lot: "recap.Lot | None" = None) -> str:
    """
    Transmet un dossier LICIEL. Renvoie un message. Le résultat (analyse
    Opticheck comprise) est ajouté à `lot` pour le mail récapitulatif.
    """
    summary = liciel.parse_dpe_summary(dossier)
    xml_files = liciel.get_xml_files(dossier)
    result = send.send_dpe(xml_files, summary, cfg, dossier=dossier)
    if lot is not None:
        lot.transmis(summary, "LICIEL", result)
    return result


def _send_one_adn(src, dossier_row: dict, mission_row: dict, cfg: dict,
                  icon: pystray.Icon | None = None,
                  lot: "recap.Lot | None" = None) -> str:
    """
    Transmet un DPE Analys'immo et programme son rapport. Le DPE est extrait
    des bases ADN à l'instant de l'envoi : c'est toujours l'état enregistré
    le plus récent qui part, sans qu'il soit besoin de le télétransmettre à
    l'ADEME au préalable.
    """
    import adn
    summary = adn.parse_dpe_summary(src, dossier_row, mission_row)
    try:
        payload = adn.read_dpe(src, dossier_row, mission_row)
    except Exception as e:
        # La saisie brute n'est qu'un complément du XML ADEME : son échec
        # (table inattendue d'une version d'ADN) ne bloque pas l'envoi.
        payload = {"meta": {"source": "Analysimmo (ADN)",
                            "erreur_extraction": str(e)[:500]}}
    # XML ADEME au modèle officiel, comme pour LICIEL : c'est lui qu'Opticheck
    # sait déjà lire. La saisie brute reste jointe en complément.
    ademe, rapport = send._try_ademe_adn(src, dossier_row, mission_row, cfg)
    officiel = adn.official_xml(src, dossier_row)
    result = send.send_adn_dpe(summary, payload, cfg, ademe, rapport, officiel)
    if lot is not None:
        remarques = []
        if (rapport or {}).get("xml_officiel_anterieur_derniere_modif"):
            remarques.append(
                "Le XML analysé est l'export officiel d'Analys'immo, antérieur à "
                "la dernière modification du dossier : relancez le calcul dans "
                "Analys'immo puis transmettez à nouveau pour une analyse à jour.")
        lot.transmis(summary, "Analys'immo", result, remarques)
    return result


def _envoi(lot: "recap.Lot", nom: str, logiciel: str, fn):
    """
    Exécute un envoi unitaire en consignant un éventuel échec dans le lot.
    ReauthRequired est relancée telle quelle (l'IHM propose la reconnexion et
    l'envoi sera rejoué : ce n'est pas un échec à signaler par mail).
    """
    try:
        return fn()
    except auth.ReauthRequired:
        raise
    except Exception as e:
        lot.echec(nom, logiciel, e)
        raise


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

    def _one(lot):
        return _envoi(lot, summary.get("dossier"), "Analys'immo",
                      lambda: _send_one_adn(src, dossier, mission, cfg, icon, lot))

    def do_send() -> str:
        lot = recap.Lot()
        try:
            return _one(lot)
        except auth.ReauthRequired:
            state["reauth"] = True
            return ("Session Espace Pro expirée pendant l'envoi.\n"
                    "Une reconnexion va vous être proposée.")
        except Exception as e:
            return f"Erreur lors de l'envoi :\n{e}"
        finally:
            recap.dispatch(lot, cfg, icon)

    # Une source en base n'a pas de fichiers à dénombrer : on annonce la
    # mission elle-même comme unique pièce transmise.
    dialog.show_confirmation_dialog(summary, 1, do_send)

    if state["reauth"] and _propose_reconnect(
        icon, cfg, "Votre session Espace Pro a expiré.\n"
                   "Reconnectez-vous pour transmettre ce DPE."
    ):
        lot = recap.Lot()
        try:
            dialog.show_message(_one(lot))
        except Exception as e:
            dialog.show_message(f"Erreur lors de l'envoi :\n{e}")
        finally:
            recap.dispatch(lot, cfg, icon)


def _on_select_adn(icon: pystray.Icon, cfg: dict) -> None:
    """Ouvre la liste des DPE Analys'immo récents pour en choisir un ou plusieurs."""
    _set_alert(icon, False)
    if not _ensure_authenticated(icon, cfg):
        return

    import adn
    try:
        src = adn.open_source(cfg)
        enriched = adn.dossiers_avec_resume(
            src, limit=cfg.get("dossier_list_limit", 30),
            resume_dossiers=cfg.get("adn_resume_dossiers", 5))
    except Exception as e:
        dialog.show_message(f"Lecture d'Analys'immo impossible :\n\n{e}")
        return

    if not enriched:
        dialog.show_message(
            "Aucun dossier Analys'immo ne porte de mission DPE.")
        return

    state = {"reauth": False}

    def on_send(selection: list[dict]) -> str:
        lot = recap.Lot()
        try:
            return _lot_adn(selection, lot)
        finally:
            recap.dispatch(lot, cfg, icon)

    def _lot_adn(selection: list[dict], lot: "recap.Lot") -> str:
        ok, errors = [], []
        for item in selection:
            try:
                _envoi(lot, item["dossier"], "Analys'immo",
                       lambda: _send_one_adn(src, item["_dossier_row"],
                                             item["_mission_row"], cfg, icon, lot))
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

    def load_all() -> list[dict]:
        # Tous les dossiers, résumé léger : trois requêtes au total.
        return adn.dossiers_avec_resume(src, limit=None, resume_dossiers=0)

    dialog.show_dossier_selection_dialog(enriched, on_send, load_all=load_all)

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

    def _one(lot):
        return _envoi(lot, summary.get("dossier") or dossier.name, "LICIEL",
                      lambda: _send_one(dossier, cfg, icon, lot))

    def do_send() -> str:
        lot = recap.Lot()
        try:
            return _one(lot)
        except auth.ReauthRequired:
            state["reauth"] = True
            return ("Session Espace Pro expirée pendant l'envoi.\n"
                    "Une reconnexion va vous être proposée.")
        except Exception as e:
            return f"Erreur lors de l'envoi :\n{e}"
        finally:
            recap.dispatch(lot, cfg, icon)

    dialog.show_confirmation_dialog(summary, len(xml_files), do_send)

    # Session expirée en cours d'envoi → proposer la reconnexion puis rejouer.
    if state["reauth"] and _propose_reconnect(
        icon, cfg, "Votre session Espace Pro a expiré.\n"
                   "Reconnectez-vous pour transmettre ce dossier."
    ):
        lot = recap.Lot()
        try:
            dialog.show_message(_one(lot))
        except Exception as e:
            dialog.show_message(f"Erreur lors de l'envoi :\n{e}")
        finally:
            recap.dispatch(lot, cfg, icon)


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
        lot = recap.Lot()
        try:
            return _lot_liciel(selection, lot)
        finally:
            recap.dispatch(lot, cfg, icon)

    def _lot_liciel(selection: list[dict], lot: "recap.Lot") -> str:
        ok, errors = [], []
        for item in selection:
            try:
                _envoi(lot, item["dossier"], "LICIEL",
                       lambda: _send_one(item["path"], cfg, icon, lot))
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
              "(LICIEL Diagnostics ou Analys'immo). La passerelle s'y "
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


def _on_adn_diagnostic(icon: pystray.Icon, cfg: dict) -> None:
    """Écrit le rapport de diagnostic Analys'immo sur le Bureau et l'ouvre."""
    icon.notify("Passerelle Optimmo",
                "Diagnostic Analys'immo en cours (jusqu'à une minute)…")
    try:
        import adn_diag
        import os
        path = adn_diag.write_report(cfg)
        os.startfile(str(path))  # noqa: S606
        icon.notify("Passerelle Optimmo",
                    f"Rapport enregistré sur le Bureau : {path.name}. "
                    "Transmettez-le au support Optimmo.")
    except Exception as e:
        dialog.show_message(f"Diagnostic impossible :\n\n{e}")


def _check_adn_at_start(icon: pystray.Icon, cfg: dict) -> None:
    """
    Vérifie dès le démarrage qu'Analys'immo est lisible, pour que l'utilisateur
    l'apprenne tout de suite plutôt qu'au moment de transmettre un DPE.
    """
    if not _adn_ready(cfg):
        return
    try:
        import adn
        adn.open_source(cfg)
    except Exception as e:
        first = str(e).splitlines()[0][:150]
        icon.notify(
            "Passerelle Optimmo",
            f"Analys'immo détecté mais illisible : {first} "
            "(menu → « Diagnostic Analys'immo… »).")


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
        *([pystray.MenuItem(
            "Diagnostic Analys'immo…",
            lambda icon, item: threading.Thread(
                target=_on_adn_diagnostic, args=(icon, cfg), daemon=True
            ).start(),
        )] if _adn_ready(cfg) else []),
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

    _check_adn_at_start(icon, cfg)

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
    _adn_label["icon"] = icon
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
        try:
            import adn_db
            adn_db.cleanup_snapshots()
        except Exception:
            pass
        updater.finalize_pending()
