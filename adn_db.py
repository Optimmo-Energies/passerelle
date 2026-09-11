"""
Accès en lecture seule aux bases Analys'immo (ADN), quel que soit le moteur.

Analys'immo se décline en deux stockages selon l'installation :
  - **SQL Server / LocalDB** : le cas des installations en réseau ou en synchro
    (bases `ADN_DIAG`, `ADN_DIAG_DPE2012`, `ADN_RG`) ;
  - **SQL Server Compact** (`.sdf`) : le cas particulier des installations
    d'évaluation.

Les deux moteurs sont pilotés via pythonnet : `System.Data.SqlClient` pour
SQL Server, `System.Data.SqlServerCe` pour les `.sdf`. Aucun pilote ODBC n'est
requis — seul le .NET Framework, que l'installation d'ADN impose déjà.

La configuration de connexion est lue dans `sd.config`, à la racine du dossier
d'installation d'ADN. Le mot de passe qu'on y trouve n'est pas toujours
exploitable (il peut être obfusqué selon la version) : on essaie donc
successivement l'authentification SQL puis l'authentification Windows.

Toutes les requêtes émises par ce module sont des SELECT : `query()` refuse
tout autre verbe, pour garantir qu'on ne corrompra jamais les dossiers du
client.
"""
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Emplacements probables du dossier d'installation d'Analys'immo.
DEFAULT_ROOTS = (r"C:\ADN", r"C:\ADN_Evaluation", r"D:\ADN", r"D:\ADN_Evaluation")

_SSCE_DLL_DIR = r"C:\Program Files\Microsoft SQL Server Compact Edition\v3.5\Desktop"

# Bases logiques utilisées par la passerelle et leur rôle.
DB_DIAG = "ADN_DIAG"            # dossiers, missions, interlocuteurs
DB_DPE = "ADN_DIAG_DPE2012"     # coeur du DPE (enveloppe, installations, calculs)
DB_RG = "ADN_RG"                # référentiel : utilisateurs, employés, société

_SELECT_ONLY = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)

_clr_loaded = {"sql": False, "ce": False}


class AdnUnavailable(RuntimeError):
    """Aucune base Analys'immo lisible n'a pu être atteinte."""


# ── Chargement des assemblys .NET ────────────────────────────────────────────
def _load_sqlclient():
    if _clr_loaded["sql"]:
        return
    import clr  # noqa: PLC0415
    clr.AddReference("System.Data")
    _clr_loaded["sql"] = True


def _load_sqlce():
    if _clr_loaded["ce"]:
        return
    if _SSCE_DLL_DIR not in sys.path:
        sys.path.append(_SSCE_DLL_DIR)
    import clr  # noqa: PLC0415
    clr.AddReference("System.Data.SqlServerCe")
    _clr_loaded["ce"] = True


# ── Lecture de sd.config ─────────────────────────────────────────────────────
def read_sd_config(adn_root: str) -> dict:
    """
    Extrait la configuration de connexion d'un `sd.config`. Retourne un dict
    vide si le fichier est absent ou illisible.
    """
    path = Path(adn_root) / "sd.config"
    if not path.is_file():
        return {}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except (ET.ParseError, OSError):
        return {}

    # `defaultSdsIndex` désigne la connexion active parmi `listeSds`.
    try:
        idx = int((root.findtext("defaultSdsIndex") or "0").strip())
    except ValueError:
        idx = 0
    sds_list = root.findall("./listeSds/SDS")
    if not sds_list:
        return {}
    sds = sds_list[idx] if 0 <= idx < len(sds_list) else sds_list[0]

    def txt(tag: str) -> str:
        return (sds.findtext(tag) or "").strip()

    return {
        "name": txt("Name"),
        "server": txt("serverName"),
        "auth": txt("authentificationType"),
        "user": txt("connectionName"),
        "password": txt("passWord"),
        "localdb_instance": txt("NameInstanceLocalDB"),
        "is_localdb": txt("IsLocalDB").lower() == "true",
        "sdl_dir": txt("SDLRelativeDirectory"),
        "port": txt("portNumber"),
    }


# ── Découverte de l'installation ─────────────────────────────────────────────
def is_adn_root(path: str) -> bool:
    """
    Vrai si `path` est bien un dossier d'installation d'Analys'immo, c'est-à-dire
    s'il contient `ADN.exe` (l'exécutable) ou `sd.config` (sa configuration).
    """
    if not path:
        return False
    d = Path(path)
    return d.is_dir() and ((d / "ADN.exe").is_file()
                           or (d / "sd.config").is_file())


def find_adn_root(hint: str = "", fallback: bool = True) -> str | None:
    """
    Localise le dossier d'installation d'Analys'immo. `hint` (chemin retenu en
    configuration) est essayé en premier ; avec `fallback`, les emplacements
    d'installation habituels sont ensuite balayés.

    Passer `fallback=False` pour vérifier un chemin précis sans jamais
    retomber sur une autre installation — c'est ce que fait la reconnaissance
    d'un dossier choisi par l'utilisateur.
    """
    candidates = [hint] if hint else []
    if fallback:
        candidates += list(DEFAULT_ROOTS)
    for cand in candidates:
        if is_adn_root(cand):
            return str(Path(cand))
    return None


def find_sdf(adn_root: str) -> Path | None:
    """
    Cherche une base `ADN_DIAG.sdf` sous le dossier de synchro de l'install.
    Ne concerne que les installations SQL Server Compact.
    """
    root = Path(adn_root)
    if not root.is_dir():
        return None
    found = sorted(root.glob("Synchro/*/ADN_DIAG.sdf")) or \
        sorted(root.glob("**/ADN_DIAG.sdf"))
    if not found:
        return None
    # Une base non « SDLDEMO » (démo) prime, puis la plus récemment modifiée.
    def rank(p: Path):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        return ("sdldemo" not in str(p).lower(), mtime)
    return max(found, key=rank)


# ── Chaînes de connexion candidates ──────────────────────────────────────────
def _candidates(sd: dict, override: str = "") -> list[tuple[str, str]]:
    """
    Chaînes de connexion à essayer, dans l'ordre, sous forme
    (libellé, chaîne). Le libellé sert aux messages d'erreur.
    """
    out: list[tuple[str, str]] = []
    if override:
        out.append(("chaîne fournie par la configuration", override))

    if sd.get("is_localdb") and sd.get("localdb_instance"):
        server = f"(localdb)\\{sd['localdb_instance']}"
        out.append((f"LocalDB {server}",
                    f"Data Source={server};Integrated Security=True;"))

    server = sd.get("server")
    if server:
        user, pwd = sd.get("user"), sd.get("password")
        if (sd.get("auth") or "").lower().startswith("sql") and user:
            out.append((f"{server} (compte SQL « {user} »)",
                        f"Data Source={server};User ID={user};Password={pwd};"))
        out.append((f"{server} (authentification Windows)",
                    f"Data Source={server};Integrated Security=True;"))
    return out


# ── Connexion SQL Server ─────────────────────────────────────────────────────
class SqlServerSource:
    """Bases Analys'immo hébergées par SQL Server ou LocalDB."""

    kind = "sqlserver"

    def __init__(self, conn_str: str, label: str = ""):
        self.conn_str = conn_str.rstrip(";") + ";"
        self.label = label or conn_str
        self._db_names: dict[str, str] = {}

    def _open(self, database: str = ""):
        _load_sqlclient()
        from System.Data.SqlClient import SqlConnection  # noqa: PLC0415
        cs = self.conn_str
        if database:
            cs += f"Initial Catalog={database};"
        # Sans timeout explicite, une instance injoignable bloque 15 s.
        if "connect timeout" not in cs.lower():
            cs += "Connect Timeout=8;"
        conn = SqlConnection(cs)
        conn.Open()
        return conn

    def resolve_db(self, logical: str) -> str:
        """
        Nom réel d'une base logique. Les installs en synchro suffixent les
        noms (`ADN_DIAG_SDL1`…) : on retient la correspondance exacte si elle
        existe, sinon la première base qui commence par le nom logique.
        """
        if logical in self._db_names:
            return self._db_names[logical]
        rows = self.query("SELECT name FROM sys.databases", database="master")
        names = [r["name"] for r in rows]
        exact = [n for n in names if n.lower() == logical.lower()]
        prefixed = sorted(n for n in names if n.lower().startswith(logical.lower()))
        # `ADN_DIAG` est un préfixe de `ADN_DIAG_DPE2012` : l'exact prime.
        resolved = exact[0] if exact else (prefixed[0] if prefixed else logical)
        self._db_names[logical] = resolved
        return resolved

    def query(self, sql: str, database: str = "", params: dict | None = None
              ) -> list[dict]:
        if not _SELECT_ONLY.match(sql):
            raise ValueError("adn_db est en lecture seule : SELECT uniquement")
        _load_sqlclient()
        from System.Data.SqlClient import SqlCommand  # noqa: PLC0415
        conn = self._open(database)
        try:
            cmd = SqlCommand(sql, conn)
            for key, value in (params or {}).items():
                cmd.Parameters.AddWithValue(f"@{key}", _net(value))
            return _read_all(cmd)
        finally:
            conn.Close()

    def probe(self) -> None:
        """Vérifie que la source répond et expose bien les bases attendues."""
        self.resolve_db(DB_DIAG)


# ── Connexion SQL Server Compact (.sdf) ──────────────────────────────────────
class SdfSource:
    """
    Bases Analys'immo au format `.sdf`. On travaille sur une **copie** du
    fichier : ADN le tient ouvert en écriture et refuserait l'accès partagé.
    """

    kind = "sdf"

    def __init__(self, diag_sdf: str):
        self.diag = Path(diag_sdf)
        self.label = str(self.diag)

    def _path_for(self, logical: str) -> Path:
        if logical == DB_DIAG:
            return self.diag
        return self.diag.with_name(f"{logical}.sdf")

    def resolve_db(self, logical: str) -> str:
        return logical

    def query(self, sql: str, database: str = "", params: dict | None = None
              ) -> list[dict]:
        if not _SELECT_ONLY.match(sql):
            raise ValueError("adn_db est en lecture seule : SELECT uniquement")
        _load_sqlce()
        from System.Data.SqlServerCe import (  # noqa: PLC0415
            SqlCeCommand, SqlCeConnection,
        )
        src = self._path_for(database or DB_DIAG)
        if not src.is_file():
            raise AdnUnavailable(f"base Analys'immo absente : {src}")
        tmp = tempfile.mktemp(suffix=".sdf")
        shutil.copy2(src, tmp)
        conn = SqlCeConnection(f"Data Source={tmp};")
        try:
            conn.Open()
            cmd = SqlCeCommand(sql, conn)
            for key, value in (params or {}).items():
                cmd.Parameters.AddWithValue(f"@{key}", _net(value))
            return _read_all(cmd)
        finally:
            try:
                conn.Close()
            finally:
                Path(tmp).unlink(missing_ok=True)

    def probe(self) -> None:
        self.query("SELECT TOP(1) idDossier FROM Dossier", database=DB_DIAG)


# ── Lecture d'un jeu de résultats .NET ───────────────────────────────────────
def _read_all(cmd) -> list[dict]:
    """
    Matérialise un DataReader en liste de dicts Python. Les valeurs sont
    converties en types Python simples (str/int/float/bool/None) pour rester
    sérialisables en JSON.
    """
    reader = cmd.ExecuteReader()
    try:
        names = [reader.GetName(i) for i in range(reader.FieldCount)]
        rows = []
        while reader.Read():
            row = {}
            for i, name in enumerate(names):
                row[name] = None if reader.IsDBNull(i) else _py(reader.GetValue(i))
            rows.append(row)
        return rows
    finally:
        reader.Close()


def _net(value):
    """
    Convertit une valeur Python en type .NET explicite. pythonnet ne sait pas
    inférer le type d'un `int` Python pour un paramètre SQL : sans cette
    conversion, `ExecuteReader` lève « aucun mappage n'existe à partir du type
    d'objet Python.Runtime.PyInt ».
    """
    _load_sqlclient()
    from System import Boolean, DBNull, Double, Int32, Int64, String  # noqa: PLC0415

    if value is None:
        return DBNull.Value
    if isinstance(value, bool):
        return Boolean(value)
    if isinstance(value, int):
        return Int32(value) if -2147483648 <= value <= 2147483647 else Int64(value)
    if isinstance(value, float):
        return Double(value)
    return String(str(value))


def _py(value):
    """Convertit une valeur .NET en équivalent Python sérialisable."""
    tname = type(value).__name__
    if tname in ("Boolean", "bool"):
        return bool(value)
    if tname in ("Int16", "Int32", "Int64", "Byte", "SByte", "int"):
        return int(value)
    if tname in ("Single", "Double", "float"):
        return float(value)
    if tname == "Decimal":
        return float(str(value).replace(",", "."))
    if tname == "DateTime":
        return value.ToString("yyyy-MM-ddTHH:mm:ss")
    if tname == "Guid":
        return str(value)
    if tname in ("Byte[]",):
        return None  # blobs volontairement écartés
    return str(value)


# ── Point d'entrée ───────────────────────────────────────────────────────────
def connect(cfg: dict | None = None):
    """
    Ouvre la meilleure source Analys'immo disponible et la retourne.

    Ordre de préférence : SQL Server / LocalDB (installations courantes), puis
    une base `.sdf` (installations d'évaluation). Lève `AdnUnavailable` avec le
    détail des tentatives si rien n'est joignable — c'est ce message qui est
    remonté à l'utilisateur, il doit donc rester lisible.
    """
    cfg = cfg or {}
    tried: list[str] = []

    root = find_adn_root(cfg.get("adn_root", ""))
    if root is None:
        raise AdnUnavailable(
            "Analys'immo (ADN) n'a pas été trouvé sur ce poste.\n"
            "Indiquez son dossier d'installation via « Configurer le logiciel "
            "de diagnostic… »."
        )

    sd = read_sd_config(root)
    for label, cs in _candidates(sd, cfg.get("adn_connection_string", "")):
        src = SqlServerSource(cs, label)
        try:
            src.probe()
            src.root = root
            src.sd_config = sd
            return src
        except Exception as e:
            tried.append(f"  • {label} : {_short(e)}")

    # Repli : installation d'évaluation en SQL Server Compact.
    sdf = cfg.get("analysimo_sdf") or ""
    sdf_path = Path(sdf) if sdf and Path(sdf).is_file() else find_sdf(root)
    if sdf_path is not None:
        src = SdfSource(str(sdf_path))
        try:
            src.probe()
            src.root = root
            src.sd_config = sd
            return src
        except Exception as e:
            tried.append(f"  • base {sdf_path.name} : {_short(e)}")

    raise AdnUnavailable(
        f"Analys'immo a été trouvé dans {root}, mais aucune de ses bases "
        "n'est accessible :\n" + "\n".join(tried or ["  • aucune piste testée"])
    )


def _short(exc: Exception) -> str:
    """Première ligne utile d'une exception .NET, souvent très verbeuse."""
    msg = str(exc).replace("\r", " ").replace("\n", " ")
    msg = re.sub(r"\s+", " ", msg).strip()
    msg = re.sub(r"^.*?\(0x[0-9A-Fa-f]+\):\s*", "", msg)
    return msg[:160] or type(exc).__name__


def available(cfg: dict | None = None) -> bool:
    """Vrai si une base Analys'immo est joignable (sans lever d'exception)."""
    try:
        connect(cfg)
        return True
    except Exception:
        return False
