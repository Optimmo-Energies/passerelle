import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".optimo_bridge" / "config.json"

DEFAULTS = {
    # dpe_ingest appelé en direct (comme auth_api_url), pas via le repo backend
    # (api.optimmo-energies.com = service backend, distinct de dpe_ingest).
    "api_url": "https://dpe-ingest-xfyprtzkyq-ew.a.run.app/dpe_en_cours/upload",
    "api_key": "",
    "liciel_root": r"C:\LICIEL_Diagnostics",
    "demo_mode": False,  # True = sauvegarde locale, False = envoi API réel
    "output_dir": str(Path.home() / "Desktop" / "optimmo_exports"),
    # Analys'immo (ADN). `adn_root` = dossier d'installation ; le moteur de
    # base (SQL Server, LocalDB ou .sdf) est déduit de son sd.config.
    # Laissé vide → détection automatique (C:\ADN, C:\ADN_Evaluation…).
    "adn_root": "",
    # Chaîne de connexion .NET forçant l'accès aux bases ADN, si la détection
    # automatique échoue (instance nommée atypique, compte SQL dédié…).
    # Ex. : "Data Source=SRV\\SQLATLIBITUM;User ID=lecteur;Password=***;"
    "adn_connection_string": "",
    # Ancien réglage : chemin direct d'une base ADN_DIAG.sdf. Conservé en
    # repli pour les installations d'évaluation en SQL Server Compact.
    "analysimo_sdf": "",
    "reminder_debounce_seconds": 120,  # délai après dernière modif XML avant rappel
    # Intervalle de sondage de la base Analys'immo pour le rappel de
    # transmission (aucun fichier à surveiller : ADN écrit en base).
    "adn_poll_seconds": 180,
    # Bloc <diagnostiqueur> du XML ADEME (modèle DPE_complet) : ces valeurs
    # complètent/priment sur les données société LICIEL.
    "diagnostiqueur": {
        "nom": "",
        "prenom": "",
        "mail": "",
        "telephone": "",
        "adresse": "",
        "entreprise": "",
        "numero_certification": "",
        "organisme_certificateur": "",
    },
    # Consentement formulaire RGPD (champ obligatoire du XSD depuis 2024,
    # non généré par LICIEL) : 0 = absence, 1 = fourni, 2 = non requis.
    "ademe_consentement_formulaire": "0",
    # Champs supplémentaires injectés dans le XML ADEME : chemin relatif à la
    # racine <dpe> → valeur. Ex. : {"administratif/siren_proprietaire": "123456789"}
    "ademe_champs_supplementaires": {},
    "dossier_list_limit": 30,  # nb de dossiers récents listés dans la sélection
    # Analys'immo : seuls les DPE des N dossiers les plus récents reçoivent le
    # résumé complet (plusieurs requêtes chacun) ; les autres sont listés avec
    # leur n° ADEME, sans les classes. « Chercher dans tous les dossiers »
    # lève la limite de dossier_list_limit.
    "adn_resume_dossiers": 5,
    # Authentification via l'Espace Pro (OAuth loopback + PKCE).
    # require_auth=False → comportement historique (aucun login requis) tant que
    # les endpoints web (/passerelle/authorize, /desktop/token) ne sont pas livrés.
    "require_auth": True,
    "webapp_url": "https://app-espace-pro.optimmo-energies.com",
    "auth_api_url": "https://authentication-service-xfyprtzkyq-ew.a.run.app",
    "espace_pro_api_url": "https://api-espace-pro.optimmo-energies.com",
    "start_at_boot": True,     # lancement auto à l'ouverture de session Windows
    "auto_update": True,       # vérifier les mises à jour au démarrage
    # URL stable : pointe toujours vers le dernier release GitHub publié.
    "update_url": "https://github.com/Optimmo-Energies/passerelle/releases/latest/download/latest.json",
    # Rapport Opticheck par email
    "opticheck_api_url": "https://api.optimmo-energies.com",
    # Lien ouvert au clic sur le toast Windows de fin d'analyse.
    "opticheck_link_url": "https://app-espace-pro.optimmo-energies.com/reseau-technicien",
    "opticheck_api_key": "",   # laissé vide → utilise api_key
    # Mail récapitulatif au diagnostiqueur après chaque transmission (un mail
    # par envoi, tous dossiers et écarts compris), envoyé par dpe_ingest.
    "recap_email": True,
    # Vide → déduit de api_url (…/dpe_en_cours/upload → …/recap_email).
    "recap_email_url": "",
    "report_delay_seconds": 60,
    "report_to": "gabriel.koutchinsky@optimmo-energies.com",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "gabriel.koutchinsky@optimmo-energies.com",
    "smtp_password": "",  # défini localement dans ~/.optimo_bridge/config.json
    "smtp_from": "gabriel.koutchinsky@optimmo-energies.com",
}


# Version du schéma de configuration. À incrémenter quand une migration des
# config.json existants est nécessaire (voir `_migrate`).
CONFIG_VERSION = 2

# Réglages imposés une fois aux installations existantes lors de la migration
# vers CONFIG_VERSION 2 : un poste installé avec une ancienne version a pu
# enregistrer le mode démo, l'absence d'authentification ou des URL de service
# provisoires (placeholder api.optimmo…), qui primeraient sinon pour toujours
# sur les défauts de production (`{**DEFAULTS, **config.json}`).
_PROD_KEYS_V2 = (
    "demo_mode", "require_auth", "api_url", "update_url", "auth_api_url",
    "webapp_url", "espace_pro_api_url", "opticheck_api_url",
    "opticheck_link_url", "auto_update",
)


def _migrate(stored: dict) -> tuple[dict, bool]:
    """Met à niveau un config.json ancien. Renvoie (config, modifiée ?)."""
    version = stored.get("config_version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    changed = False
    if version < 2:
        for key in _PROD_KEYS_V2:
            if stored.get(key) != DEFAULTS[key]:
                stored[key] = DEFAULTS[key]
                changed = True
        stored["config_version"] = 2
        changed = True
    return stored, changed


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8-sig") as f:
                stored = json.load(f)
            if not isinstance(stored, dict):
                raise ValueError("config.json n'est pas un objet JSON")
        except (OSError, ValueError):
            # config.json corrompu (édition manuelle, écriture interrompue) :
            # on le met de côté et on repart des défauts plutôt que de
            # rendre la Passerelle inutilisable au démarrage.
            try:
                CONFIG_FILE.replace(CONFIG_FILE.with_suffix(".json.corrompu"))
            except OSError:
                pass
            return {**DEFAULTS, "config_version": CONFIG_VERSION}
        stored, changed = _migrate(stored)
        cfg = {**DEFAULTS, **stored}
        # Sous-dictionnaire : compléter les clés manquantes plutôt que remplacer.
        diag = stored.get("diagnostiqueur")
        cfg["diagnostiqueur"] = {**DEFAULTS["diagnostiqueur"],
                                 **(diag if isinstance(diag, dict) else {})}
        if changed:
            try:
                save(cfg)
            except OSError:
                pass
        return cfg
    return {**DEFAULTS, "config_version": CONFIG_VERSION}


def save(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Écriture atomique : un arrêt brutal pendant l'écriture ne doit jamais
    # laisser un config.json tronqué.
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)
