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
    "analysimo_sdf": r"C:\ADN_Evaluation\Synchro\SDLDEMO\ADN_DIAG.sdf",
    "reminder_debounce_seconds": 120,  # délai après dernière modif XML avant rappel
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
    "report_delay_seconds": 60,
    "report_to": "gabriel.koutchinsky@optimmo-energies.com",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "gabriel.koutchinsky@optimmo-energies.com",
    "smtp_password": "",  # défini localement dans ~/.optimo_bridge/config.json
    "smtp_from": "gabriel.koutchinsky@optimmo-energies.com",
}


def load() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    return DEFAULTS.copy()


def save(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
