import threading
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
import updater
import watcher
from version import __version__

_alert = {"active": False}
# État de la surveillance LICIEL, conservé au niveau module pour permettre son
# redémarrage après une (re)configuration du logiciel de diagnostic.
_watch = {"obs": None, "on_idle": None, "debounce": 120}


def _apply_source(cfg: dict, picked: tuple[str, str]) -> None:
    """Reporte le logiciel détecté (source, chemin) dans la configuration."""
    source, value = picked
    if source == "liciel":
        cfg["liciel_root"] = value
    elif source == "adn":
        cfg["analysimo_sdf"] = value


def _restart_watch(cfg: dict) -> None:
    """
    (Re)démarre la surveillance du dossier LICIEL. Reste silencieux si le
    dossier est absent (cas d'un utilisateur ADN Evaluation uniquement) :
    l'envoi manuel reste disponible.
    """
    if _watch["obs"] is not None:
        try:
            _watch["obs"].stop()
        except Exception:
            pass
        _watch["obs"] = None
    if _watch["on_idle"] is None:
        return
    _watch["obs"] = watcher.start(
        cfg["liciel_root"], _watch["on_idle"], _watch["debounce"]
    )


def _make_icon(alert: bool = False) -> Image.Image:
    if alert:
        return icon_gen.make_tray_icon_alert()
    ico_path = Path(__file__).parent / "icon_tray.png"
    if ico_path.exists():
        return Image.open(ico_path).convert("RGB")
    return icon_gen.make_tray_icon()


# La transmission des DPE ADN Evaluation passe par l'outil ADN dédié (plugin
# .NET qui reconstruit le XML DPE depuis les bases ADN), pas par cette
# passerelle, réservée à LICIEL. Ce message oriente l'utilisateur ADN.
_ADN_GUIDANCE = (
    "Vous utilisez ADN Evaluation (Analys'immo).\n\n"
    "La transmission des DPE ADN à Opticheck se fait via l'outil ADN dédié, "
    "et non par cette passerelle qui est réservée à LICIEL.\n\n"
    "Contactez Optimmo Énergies pour installer l'outil ADN."
)


def _liciel_ready(cfg: dict) -> bool:
    return diag_setup.liciel_present(cfg.get("liciel_root", ""))


def _adn_only(cfg: dict) -> bool:
    """Utilisateur ADN Evaluation sans LICIEL sur le poste."""
    return not _liciel_ready(cfg) and diag_setup.adn_present(
        cfg.get("analysimo_sdf", ""))


def _get_dossier_label(cfg: dict) -> str:
    dossier = liciel.find_latest_dossier(cfg["liciel_root"])
    return f"Dossier actif : {dossier.name}" if dossier else "Aucun dossier LICIEL trouvé"


def _status_label(cfg: dict) -> str:
    """Ligne d'état en tête de menu, adaptée au logiciel de diagnostic présent."""
    if _liciel_ready(cfg):
        return _get_dossier_label(cfg)
    if _adn_only(cfg):
        return "ADN Evaluation — transmission via l'outil ADN dédié"
    return "Aucun logiciel de diagnostic configuré"


def _set_alert(icon: pystray.Icon, active: bool) -> None:
    _alert["active"] = active
    icon.icon = _make_icon(alert=active)
    icon.title = (
        "Optimmo Passerelle — Dossier en attente de transmission"
        if active else
        "Optimmo Passerelle"
    )


def _send_one(dossier: Path, cfg: dict, icon: pystray.Icon | None = None) -> str:
    """Transmet un dossier LICIEL et programme son rapport. Renvoie un message."""
    summary = liciel.parse_dpe_summary(dossier)
    xml_files = liciel.get_xml_files(dossier)
    result = send.send_dpe(xml_files, summary, cfg, dossier=dossier)
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
        icon.notify("Optimmo Passerelle",
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


def _on_adn_info(icon: pystray.Icon, cfg: dict) -> None:
    """Oriente l'utilisateur ADN vers l'outil dédié."""
    dialog.show_message(_ADN_GUIDANCE)


def _on_send(icon: pystray.Icon, cfg: dict) -> None:
    """Envoi rapide du dernier dossier (avec mission DPE)."""
    _set_alert(icon, False)
    if not _liciel_ready(cfg):
        dialog.show_message(_ADN_GUIDANCE if _adn_only(cfg)
                            else "Aucun logiciel de diagnostic configuré.")
        return
    if not _ensure_authenticated(icon, cfg):
        return

    dossier = liciel.find_latest_dossier(cfg["liciel_root"])
    if dossier is None:
        dialog.show_confirmation_dialog(
            {"dossier": "Introuvable"}, 0,
            lambda: "Erreur : aucun dossier LICIEL détecté."
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
        dialog.show_message(_ADN_GUIDANCE if _adn_only(cfg)
                            else "Aucun logiciel de diagnostic configuré.")
        return
    if not _ensure_authenticated(icon, cfg):
        return
    limit = cfg.get("dossier_list_limit", 30)
    dossiers = liciel.list_dossiers(cfg["liciel_root"], limit=limit)
    if not dossiers:
        dialog.show_confirmation_dialog(
            {"dossier": "Introuvable"}, 0,
            lambda: "Erreur : aucun dossier LICIEL détecté."
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
    icon.notify("Optimmo Passerelle", f"{label} configuré.")


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
        icon.notify("Optimmo Passerelle",
                    f"Connecté{f' : {name}' if name else ''}.")
    else:
        icon.notify("Optimmo Passerelle", "Échec de la connexion.")
    icon.menu = _build_menu(icon, cfg)


def _on_logout(icon: pystray.Icon, cfg: dict) -> None:
    auth.logout()
    icon.notify("Optimmo Passerelle", "Déconnecté.")
    icon.menu = _build_menu(icon, cfg)


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
    Actions de transmission adaptées au logiciel présent :
      - LICIEL       → envoi rapide + sélection de dossiers (rôle de la passerelle) ;
      - ADN Ev. seul → orientation vers l'outil ADN dédié (pas de menu LICIEL) ;
      - aucun        → aucune action (l'utilisateur doit configurer un logiciel).
    """
    if _liciel_ready(cfg):
        mode_label = "[DÉMO] " if cfg.get("demo_mode") else ""
        return [
            pystray.MenuItem(
                f"{mode_label}Envoyer le dernier dossier",
                lambda icon, item: threading.Thread(
                    target=_on_send, args=(icon, cfg), daemon=True
                ).start(),
                enabled=lambda item: _is_logged_in(cfg),
            ),
            pystray.MenuItem(
                f"{mode_label}Choisir les dossiers à envoyer…",
                lambda icon, item: threading.Thread(
                    target=_on_select, args=(icon, cfg), daemon=True
                ).start(),
                enabled=lambda item: _is_logged_in(cfg),
            ),
        ]
    if _adn_only(cfg):
        return [
            pystray.MenuItem(
                "Transmettre un DPE ADN…",
                lambda icon, item: threading.Thread(
                    target=_on_adn_info, args=(icon, cfg), daemon=True
                ).start(),
            ),
        ]
    return []


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

    if cfg.get("require_auth"):
        if auth.is_authenticated():
            auth.current_user(cfg)  # peuple le cache (réseau, en arrière-plan)
            icon.menu = _build_menu(icon, cfg)
        else:
            icon.notify(
                "Optimmo Passerelle",
                "Connectez-vous à l'Espace Pro pour transmettre vos DPE "
                "(menu de l'icône → « Se connecter… »).",
            )

    update = updater.check_and_prepare(cfg)
    if update:
        if update.get("pending"):
            icon.notify(
                "Optimmo Passerelle",
                f"Mise à jour {update['version']} téléchargée — "
                "elle s'installera à la fermeture de l'application.",
            )
        else:
            icon.notify(
                "Optimmo Passerelle",
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
        title=f"Optimmo Passerelle v{__version__}",
    )
    icon.menu = _build_menu(icon, cfg)

    def on_dossier_idle():
        _set_alert(icon, True)
        icon.notify(
            "Optimmo Passerelle",
            "Un dossier DPE est en cours — pensez à le transmettre avant validation.",
        )

    _watch["on_idle"] = on_dossier_idle
    _watch["debounce"] = cfg.get("reminder_debounce_seconds", 120)
    _restart_watch(cfg)

    def setup(icon_):
        icon_.visible = True
        threading.Thread(target=_post_start, args=(icon_, cfg), daemon=True).start()

    try:
        icon.run(setup=setup)
    finally:
        updater.finalize_pending()
