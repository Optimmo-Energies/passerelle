"""
Lecture de la base Analysimo (SQL Server Compact Edition .sdf) via pythonnet.
On travaille toujours sur une COPIE du SDF pour éviter le conflit avec ADN
qui tient le fichier ouvert en écriture.
"""
import shutil
import sys
import tempfile
from pathlib import Path

_SSCE_DLL_DIR = r"C:\Program Files\Microsoft SQL Server Compact Edition\v3.5\Desktop"
_SDF_DEFAULT  = r"C:\ADN_Evaluation\Synchro\SDLDEMO\ADN_DIAG.sdf"

_clr_loaded = False


def _load_clr():
    global _clr_loaded
    if _clr_loaded:
        return
    if _SSCE_DLL_DIR not in sys.path:
        sys.path.append(_SSCE_DLL_DIR)
    import clr
    clr.AddReference("System.Data.SqlServerCe")
    _clr_loaded = True


def _open_copy(sdf_path: str):
    """Copie le SDF dans un fichier temporaire et retourne une connexion ouverte."""
    _load_clr()
    from System.Data.SqlServerCe import SqlCeConnection  # noqa: PLC0415
    tmp = tempfile.mktemp(suffix=".sdf")
    shutil.copy2(sdf_path, tmp)
    conn = SqlCeConnection(f"Data Source={tmp};")
    conn.Open()
    return conn, tmp


def _query(conn, sql: str) -> list[dict]:
    from System.Data.SqlServerCe import SqlCeCommand  # noqa: PLC0415
    cmd = SqlCeCommand(sql, conn)
    reader = cmd.ExecuteReader()
    rows = []
    while reader.Read():
        rows.append({
            reader.GetName(i): (
                str(reader.GetValue(i)) if not reader.IsDBNull(i) else None
            )
            for i in range(reader.FieldCount)
        })
    reader.Close()
    return rows


def find_latest_dossier(sdf_path: str = _SDF_DEFAULT) -> dict | None:
    """Retourne le dossier Analysimo modifié le plus récemment (non supprimé)."""
    conn, tmp = _open_copy(sdf_path)
    try:
        rows = _query(
            conn,
            "SELECT TOP(1) * FROM Dossier WHERE dateSup IS NULL ORDER BY dateMaj DESC",
        )
        return rows[0] if rows else None
    finally:
        conn.Close()
        Path(tmp).unlink(missing_ok=True)


def get_dpe_missions(sdf_path: str = _SDF_DEFAULT, id_dossier: str | None = None) -> list[dict]:
    """Retourne les missions DPE du dossier, triées par date RDV décroissante."""
    conn, tmp = _open_copy(sdf_path)
    try:
        where = f"AND m.idDossier = '{id_dossier}'" if id_dossier else ""
        return _query(
            conn,
            f"""
            SELECT m.idMission, m.idDossier, m.intitule, m.idTypeMission,
                   m.dateRdv, m.statut, m.isFini, m.conclusion
            FROM Mission m
            WHERE m.dateSup IS NULL
              AND m.idTypeMission IN ('DPE','DPE2012','DPE2021','IC','D')
              {where}
            ORDER BY m.dateRdv DESC
            """,
        )
    finally:
        conn.Close()
        Path(tmp).unlink(missing_ok=True)


def parse_summary(sdf_path: str = _SDF_DEFAULT) -> dict:
    """Extrait un résumé du dossier le plus récent pour inclusion dans l'envoi."""
    dossier = find_latest_dossier(sdf_path)
    if not dossier:
        return {}

    missions = get_dpe_missions(sdf_path, dossier.get("idDossier"))

    return {
        "source":           "Analysimo",
        "reference":        dossier.get("reference"),
        "adresse":          dossier.get("adresse"),
        "code_postal":      dossier.get("codePostal"),
        "ville":            dossier.get("ville"),
        "departement":      dossier.get("departement"),
        "annee_construction": dossier.get("anneeConstruction"),
        "surface":          dossier.get("surface"),
        "usage_bien":       dossier.get("usageBien"),
        "categorie_bien":   dossier.get("categorieBien"),
        "hsp_moy":          dossier.get("hspMoy"),
        "date_commande":    dossier.get("dateCommande"),
        "date_maj":         dossier.get("dateMaj"),
        "missions_dpe":     missions,
    }
