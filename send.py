import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import requests

import ademe_xml


def _reencode_table(f: Path) -> bytes:
    """
    Ré-encode une table LICIEL (cp1252 sans déclaration) en UTF-8 avec
    déclaration XML, pour que le contenu du zip soit directement parsable.
    """
    text = ademe_xml.decode_xml_bytes(f.read_bytes())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + text).encode("utf-8")


def _build_zip(xml_files: list[Path], summary: dict,
               analysimo_data: dict | None = None,
               ademe: tuple[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in xml_files:
            z.writestr(f"XML/{f.name}", _reencode_table(f))
        if ademe:
            # XML officiel ADEME (modèle DPE_complet), enrichi et encodé UTF-8.
            name, data = ademe
            z.writestr(f"DPE_ADEME/{name}", data)
        z.writestr("liciel_summary.json",
                   json.dumps(summary, ensure_ascii=False, indent=2))
        if analysimo_data:
            z.writestr("analysimo_summary.json",
                       json.dumps(analysimo_data, ensure_ascii=False, indent=2))
    return buf.getvalue()


def _try_analysimo(cfg: dict) -> dict | None:
    """
    Résumé Analys'immo joint en complément d'un envoi LICIEL, quand les deux
    logiciels cohabitent sur le poste.

    L'absence d'Analys'immo est le cas normal sur un poste LICIEL : on
    n'encombre pas l'utilisateur. En revanche, un échec de *lecture* alors
    qu'Analys'immo est bien là est renvoyé dans la charge utile
    (`erreur_lecture`) au lieu d'être perdu — c'est ce silence qui rendait
    l'ancien comportement indébogable.
    """
    import diag_setup
    if not diag_setup.adn_present(cfg):
        return None
    try:
        import adn
        src = adn.open_source(cfg)
        dossier = adn.find_latest_dossier(src)
        if dossier is None:
            return {"source": "Analysimmo", "dossiers_dpe": 0}
        missions = adn.get_dpe_missions(src, dossier["idDossier"])
        summary = adn.parse_dpe_summary(src, dossier, missions[0]) \
            if missions else {"source": "Analysimmo"}
        return {k: v for k, v in summary.items() if not k.startswith("_")}
    except Exception as e:
        return {"source": "Analysimmo", "erreur_lecture": str(e)[:500]}


def _try_ademe(dossier: Path | None, cfg: dict) -> tuple[str, bytes] | None:
    """XML ADEME enrichi — None si le DPE n'est pas encore validé/publié."""
    if dossier is None:
        return None
    try:
        return ademe_xml.build(dossier, cfg)
    except Exception:
        return None


def _build_adn_zip(summary: dict, payload: dict,
                   ademe: tuple[str, bytes] | None = None) -> bytes:
    """
    Archive d'un DPE Analys'immo. Le DPE n'existe pas sous forme de fichiers
    sur le disque : il est extrait des bases ADN.

    `DPE_ADEME/` porte le XML au format officiel — le même emplacement et le
    même modèle que pour un envoi LICIEL, afin qu'Opticheck n'ait pas à
    distinguer les deux origines. `ADN/dpe.json` conserve la saisie brute,
    utile pour les champs sans équivalent dans le XSD.

    `summary.json` est la forme canonique du résumé ; `liciel_summary.json` en
    reste un alias, car c'est le nom que l'ingestion lit aujourd'hui.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if ademe:
            name, data = ademe
            z.writestr(f"DPE_ADEME/{name}", data)
        blob = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
        z.writestr("summary.json", blob)
        z.writestr("liciel_summary.json", blob)
        z.writestr("ADN/dpe.json",
                   json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        # Résumé détaillé côté Analys'immo, au même emplacement que lors d'un
        # envoi LICIEL enrichi : les consommateurs existants le retrouvent.
        z.writestr("analysimo_summary.json",
                   json.dumps({k: v for k, v in summary.items()
                               if not k.startswith("_")},
                              ensure_ascii=False, indent=2, default=str))
    return buf.getvalue()


def _try_ademe_adn(src, dossier: dict, mission: dict,
                   cfg: dict) -> tuple[tuple[str, bytes] | None, dict]:
    """
    XML ADEME reconstruit depuis Analys'immo. Retourne ((nom, octets), rapport)
    ou (None, rapport d'échec) : un échec de reconstruction ne doit pas empêcher
    la transmission de la saisie brute, mais il doit se voir dans le résumé.
    """
    try:
        import ademe_adn
        nom, data, rapport = ademe_adn.build(src, dossier, mission, cfg)
        return (nom, data), rapport
    except Exception as e:
        return None, {"erreur_reconstruction": str(e)[:500]}


def _deliver(zip_data: bytes, filename: str, summary: dict, cfg: dict) -> str:
    """
    Remet l'archive à Opticheck : sauvegarde locale en mode démo, sinon POST
    authentifié vers l'API d'ingestion. Partagé par les chemins LICIEL et
    Analys'immo pour que l'authentification et la gestion d'erreur soient
    identiques quelle que soit l'origine du DPE.
    """
    if cfg.get("demo_mode", True):
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / filename
        dest.write_bytes(zip_data)
        return f"[DÉMO] Fichier sauvegardé :\n{dest}"

    url = cfg["api_url"]
    headers = {}
    if cfg.get("require_auth"):
        # Authentification utilisateur via l'Espace Pro : JWT en Bearer.
        # Rafraîchit le jeton en amont s'il est expiré ; si la session ne peut
        # plus être renouvelée, on demande une reconnexion (géré par l'IHM).
        import auth
        token = auth.valid_access_token(cfg)
        if not token:
            raise auth.ReauthRequired(
                "Session Espace Pro expirée — reconnectez-vous pour transmettre."
            )
        headers["Authorization"] = f"Bearer {token}"
    elif cfg.get("api_key"):
        # Compat historique : clé d'API partagée.
        headers["x-api-key"] = cfg["api_key"]
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    files = {"dpe_zip": (filename, zip_data, "application/zip")}
    data = {"summary": json.dumps(summary, ensure_ascii=False, default=str)}

    def _post(hdrs):
        return requests.post(url, files=files, data=data, headers=hdrs, timeout=30)

    resp = _post(headers)
    if cfg.get("require_auth"):
        import auth
        # Access token expiré → refresh transparent puis un seul rejeu.
        if resp.status_code == 403 and "Access Token Is Expired" in resp.text:
            new_token = auth.refresh(cfg)
            if not new_token:
                raise auth.ReauthRequired(
                    "Session Espace Pro expirée — reconnectez-vous pour transmettre."
                )
            headers["Authorization"] = f"Bearer {new_token}"
            resp = _post(headers)
        # Jeton révoqué/invalide (401) ou toujours expiré après rejeu →
        # reconnexion nécessaire (à distinguer d'un 403 « Forbidden » de rôle).
        if resp.status_code == 401 or (
            resp.status_code == 403 and "Access Token Is Expired" in resp.text
        ):
            raise auth.ReauthRequired(
                "Session Espace Pro expirée — reconnectez-vous pour transmettre."
            )
    if not resp.ok:
        # Le corps JSON (`detail`) porte le message utile côté API — sans
        # ça, `raise_for_status()` ne renvoie que le code HTTP générique
        # (ex. « 502 Server Error: Bad Gateway »), qui ne dit rien à
        # l'utilisateur sur l'origine réelle du problème.
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        if detail:
            raise RuntimeError(detail)
        resp.raise_for_status()
    return f"Envoyé avec succès (HTTP {resp.status_code})"


def send_adn_dpe(summary: dict, payload: dict, cfg: dict,
                 ademe: tuple[str, bytes] | None = None,
                 rapport_ademe: dict | None = None) -> str:
    """
    Transmet à Opticheck un DPE réalisé sous Analys'immo (ADN).

    `summary` provient de `adn.parse_dpe_summary`, `payload` de
    `adn.read_dpe`, `ademe` de `ademe_adn.build`. Retourne un message de
    statut destiné à l'utilisateur.
    """
    # Un DPE dont le moteur 3CL n'a pas tourné n'a ni étiquette ni
    # consommations : le document ADEME serait structurellement incomplet et
    # l'ingestion le refuserait sur des contraintes de valeur minimale. Mieux
    # vaut le dire ici, en clair, que laisser l'utilisateur devant un rejet
    # illisible.
    if not summary.get("calcul_effectue", True):
        raise ValueError(
            f"Le calcul n'a pas été lancé pour le DPE {summary.get('dossier')}. "
            "Ouvrez la mission dans Analys'immo et cliquez « Lancer le calcul », "
            "puis réessayez.")

    summary = {k: v for k, v in summary.items() if not k.startswith("_")}
    volumetrie = (payload.get("meta") or {}).get("volumetrie") or {}
    summary = {**summary,
               "xml_ademe_joint": ademe is not None,
               # « reconstruit_adn » : XML au modèle officiel régénéré depuis
               # les bases Analys'immo, à distinguer d'un XML publié par
               # l'ADEME comme d'une reconstruction depuis LICIEL.
               "xml_ademe_source": "reconstruit_adn" if ademe else None,
               "xml_ademe_rapport": rapport_ademe or {},
               "adn_volumetrie": volumetrie}
    zip_data = _build_adn_zip(summary, payload, ademe)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"DPE_ADN_{_slug(summary['dossier'])}_{timestamp}.zip"
    return _deliver(zip_data, filename, summary, cfg)


def _slug(value: str) -> str:
    """Nom de fichier sûr à partir d'une référence de dossier."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "dossier"


def send_dpe(xml_files: list[Path], summary: dict, cfg: dict,
             dossier: Path | None = None) -> str:
    """
    Envoie le DPE à Optimmo ou le sauvegarde localement (mode démo).
    Retourne un message de statut.
    """
    analysimo_data = _try_analysimo(cfg)
    ademe = _try_ademe(dossier, cfg)
    # « publie » : XML nommé par le n° ADEME ; « depot » : XML de
    # télétransmission avant attribution du numéro ; « reconstruit » :
    # généré par la passerelle depuis les tables LICIEL.
    ademe_source = None
    if ademe:
        if re.match(r"^[A-Z0-9]{13}\.xml$", ademe[0]):
            ademe_source = "publie"
        elif ademe[0].startswith("reconstruit_"):
            ademe_source = "reconstruit"
        else:
            ademe_source = "depot"
    summary = {**summary, "xml_ademe_joint": ademe is not None,
               "xml_ademe_source": ademe_source}
    zip_data = _build_zip(xml_files, summary, analysimo_data, ademe)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Nom de fichier inchangé (nom de dossier LICIEL brut, espaces compris) :
    # c'est celui que l'ingestion Opticheck reçoit depuis toujours.
    filename = f"DPE_{summary['dossier']}_{timestamp}.zip"
    return _deliver(zip_data, filename, summary, cfg)
