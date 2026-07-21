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
    """Tente de lire le résumé Analysimo — retourne None si indisponible."""
    sdf = cfg.get("analysimo_sdf",
                  r"C:\ADN_Evaluation\Synchro\SDLDEMO\ADN_DIAG.sdf")
    try:
        import analysimo
        return analysimo.parse_summary(sdf)
    except Exception:
        return None


def _try_ademe(dossier: Path | None, cfg: dict) -> tuple[str, bytes] | None:
    """XML ADEME enrichi — None si le DPE n'est pas encore validé/publié."""
    if dossier is None:
        return None
    try:
        return ademe_xml.build(dossier, cfg)
    except Exception:
        return None


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
    filename = f"DPE_{summary['dossier']}_{timestamp}.zip"

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
    data = {"summary": json.dumps(summary, ensure_ascii=False)}

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
    resp.raise_for_status()
    return f"Envoyé avec succès (HTTP {resp.status_code})"
