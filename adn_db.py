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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def _running_adn_dirs() -> list[str]:
    """Dossiers des `ADN.exe` en cours d'exécution (installation réellement utilisée)."""
    try:
        import winproc  # noqa: PLC0415
        procs = winproc.find_processes(["ADN.exe"])
    except Exception:
        return []
    return [str(Path(p["path"]).parent) for p in procs if p.get("path")]


def _registry_adn_dirs() -> list[str]:
    """
    Emplacements d'Analys'immo déclarés dans le registre : entrées de
    désinstallation (InstallLocation / DisplayIcon) et « App Paths » d'ADN.exe.
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return []
    out: list[str] = []
    hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    uninstall = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                 r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
    pattern = re.compile(r"analys.?immo|\badn\b|atlibitum", re.IGNORECASE)
    for hive in hives:
        for base in uninstall:
            try:
                key = winreg.OpenKey(hive, base)
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(key, sub) as sk:
                            def val(name):
                                try:
                                    return str(winreg.QueryValueEx(sk, name)[0] or "")
                                except OSError:
                                    return ""
                            label = f"{val('DisplayName')} {val('Publisher')}"
                            if not pattern.search(label):
                                continue
                            loc = val("InstallLocation").strip().strip('"')
                            if loc:
                                out.append(loc)
                            icon = val("DisplayIcon").split(",")[0].strip().strip('"')
                            if icon:
                                out.append(str(Path(icon).parent))
                    except OSError:
                        continue
        for base in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ADN.exe",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\ADN.exe"):
            try:
                with winreg.OpenKey(hive, base) as k:
                    exe = str(winreg.QueryValueEx(k, "")[0] or "").strip('"')
                    if exe:
                        out.append(str(Path(exe).parent))
            except OSError:
                continue
    return out


def _extra_default_roots() -> list[str]:
    """Emplacements plausibles hors racine de disque (Program Files, Atlibitum)."""
    out = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.environ.get(env)
        if not base:
            continue
        for sub in ("ADN", "Atlibitum\\ADN", "Atlibitum\\Analysimmo",
                    "Analysimmo", "Analys'immo"):
            out.append(str(Path(base) / sub))
    out += [r"C:\Atlibitum\ADN", r"C:\Analysimmo", r"E:\ADN"]
    return out


def candidate_roots(hint: str = "") -> list[str]:
    """
    Dossiers d'installation candidats, du plus au moins probable : réglage de
    l'utilisateur, ADN en cours d'exécution, registre, emplacements usuels.
    Une installation complète (avec `sd.config`) passe avant une installation
    d'évaluation, à rang égal.
    """
    seen, out = set(), []
    groups = [[hint] if hint else [], _running_adn_dirs(), _registry_adn_dirs(),
              list(DEFAULT_ROOTS) + _extra_default_roots()]
    for group in groups:
        # Au sein d'un groupe, `sd.config` (installation complète) d'abord.
        ranked = sorted(group, key=lambda p: not (Path(p) / "sd.config").is_file()
                        if p else True)
        for p in ranked:
            if not p:
                continue
            key = os.path.normcase(os.path.normpath(p))
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def find_adn_root(hint: str = "", fallback: bool = True) -> str | None:
    """
    Localise le dossier d'installation d'Analys'immo. `hint` (chemin retenu en
    configuration) est essayé en premier ; avec `fallback`, sont ensuite
    essayés l'ADN en cours d'exécution, le registre puis les emplacements
    d'installation habituels.

    Passer `fallback=False` pour vérifier un chemin précis sans jamais
    retomber sur une autre installation — c'est ce que fait la reconnaissance
    d'un dossier choisi par l'utilisateur.
    """
    if not fallback:
        return str(Path(hint)) if is_adn_root(hint) else None
    if is_adn_root(hint):
        return str(Path(hint))
    # Détection coûteuse (registre, processus) : mise en cache par session,
    # sauf si rien n'a été trouvé (ADN peut être installé entre-temps).
    if _root_cache.get("value") and is_adn_root(_root_cache["value"]):
        return _root_cache["value"]
    # Échec récent : on ne rebalaye pas registre et processus à chaque
    # reconstruction du menu sur un poste sans Analys'immo.
    if time.monotonic() - _root_cache.get("miss", -1e9) < 60:
        return None
    for cand in candidate_roots(""):
        if is_adn_root(cand):
            _root_cache["value"] = str(Path(cand))
            return _root_cache["value"]
    _root_cache["miss"] = time.monotonic()
    return None


_root_cache: dict = {}


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
    if server and server.lower().startswith("(localdb)"):
        out.append((f"LocalDB {server}",
                    f"Data Source={server};Integrated Security=True;"))
    elif server:
        user, pwd = sd.get("user"), sd.get("password")
        servers = [server]
        port = (sd.get("port") or "").strip()
        # Port explicite (instance SQL Server à port fixe, sans SQL Browser).
        if port.isdigit() and port not in ("0", "1433") and "," not in server:
            servers.append(f"{server.split(chr(92))[0]},{port}")
        for srv in servers:
            if (sd.get("auth") or "").lower().startswith("sql") and user:
                out.append((f"{srv} (compte SQL « {user} »)",
                            f"Data Source={srv};User ID={user};Password={pwd};"))
            out.append((f"{srv} (authentification Windows)",
                        f"Data Source={srv};Integrated Security=True;"))

    # Repli : instances LocalDB présentes sur le poste. Couvre un sd.config
    # absent, illisible ou désignant une instance renommée lors d'une synchro.
    # Les instances « ADN » sont essayées en premier.
    known = {cs.lower() for _, cs in out}
    for inst in _localdb_instances():
        cs = f"Data Source=(localdb)\\{inst};Integrated Security=True;"
        if cs.lower() in known:
            continue
        known.add(cs.lower())
        out.append((f"LocalDB (localdb)\\{inst} (détectée)", cs))
    return out


def _sqllocaldb_exe() -> str | None:
    exe = shutil.which("SqlLocalDB.exe") or shutil.which("sqllocaldb")
    if exe:
        return exe
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if not base:
            continue
        found = sorted(Path(base).glob(
            "Microsoft SQL Server/*/Tools/Binn/SqlLocalDB.exe"), reverse=True)
        if found:
            return str(found[0])
    return None


_localdb_cache: dict[str, list[str]] = {}


def _localdb_instances() -> list[str]:
    """
    Instances LocalDB de l'utilisateur courant (`SqlLocalDB info`), celles
    d'Analys'immo en tête. Liste vide si LocalDB n'est pas installé.
    """
    if "v" in _localdb_cache:
        return _localdb_cache["v"]
    names: list[str] = []
    exe = _sqllocaldb_exe()
    if exe:
        try:
            res = subprocess.run(
                [exe, "info"], capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            text = res.stdout.decode("mbcs", errors="replace") \
                if sys.platform.startswith("win") else res.stdout.decode()
            names = [l.strip() for l in text.splitlines() if l.strip()]
        except Exception:
            names = []
    # Instances ADN d'abord (« ADN_Local_Synchro_SDL20_… »), les plus récentes
    # (suffixe horodaté) en premier ; puis les instances génériques.
    adn = sorted((n for n in names if "adn" in n.lower()), reverse=True)
    other = [n for n in names if "adn" not in n.lower()]
    _localdb_cache["v"] = adn + other
    return _localdb_cache["v"]


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
        # Une base logique plus longue (ADN_DIAG_DPE2012) ne doit jamais être
        # prise pour une variante suffixée d'une plus courte (ADN_DIAG).
        longer = [l.lower() for l in (DB_DIAG, DB_DPE, DB_RG)
                  if len(l) > len(logical) and l.lower().startswith(logical.lower())]
        prefixed = sorted(n for n in names
                          if n.lower().startswith(logical.lower())
                          and not any(n.lower().startswith(x) for x in longer))
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
        """
        Vérifie que la source répond et expose bien les bases d'Analys'immo.
        Une instance SQL joignable mais sans base ADN (LocalDB générique,
        autre logiciel) est rejetée : sinon on la retiendrait à tort.
        """
        rows = self.query("SELECT name FROM sys.databases", database="master")
        names = [str(r["name"]).lower() for r in rows]
        if not any(n == DB_DIAG.lower() or n.startswith(DB_DIAG.lower() + "_")
                   for n in names if not n.startswith(DB_DPE.lower())):
            raise AdnUnavailable("instance joignable mais sans base ADN_DIAG")
        self.resolve_db(DB_DIAG)
        self.query("SELECT TOP(1) idDossier FROM Dossier",
                   database=self.resolve_db(DB_DIAG))


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
        tmp = _sdf_snapshot(src)
        conn = SqlCeConnection(f"Data Source={tmp};Mode=Read Only;"
                               f"Temp Path={tempfile.gettempdir()};")
        try:
            conn.Open()
            cmd = SqlCeCommand(sql, conn)
            for key, value in (params or {}).items():
                cmd.Parameters.AddWithValue(f"@{key}", _net(value))
            return _read_all(cmd)
        finally:
            conn.Close()

    def probe(self) -> None:
        self.query("SELECT TOP(1) idDossier FROM Dossier", database=DB_DIAG)


_snapshots: dict[str, tuple[tuple, str]] = {}
_SNAP_DIR = Path(tempfile.gettempdir()) / "PasserelleOptimmo_adn"


def _sdf_snapshot(src: Path) -> str:
    """
    Copie de travail d'une base `.sdf`, réutilisée tant que l'original n'a pas
    changé (taille + date). Recopier 50 Mo à chaque requête rendait la liste
    des dossiers interminable sur les installations d'évaluation.
    """
    st = src.stat()
    sig = (st.st_size, st.st_mtime_ns)
    key = os.path.normcase(str(src))
    cached = _snapshots.get(key)
    if cached and cached[0] == sig and Path(cached[1]).is_file():
        return cached[1]
    _SNAP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".sdf", prefix=src.stem + "_",
                               dir=str(_SNAP_DIR))
    os.close(fd)
    shutil.copy2(src, tmp)
    if cached:
        Path(cached[1]).unlink(missing_ok=True)
    _snapshots[key] = (sig, tmp)
    return tmp


def cleanup_snapshots() -> None:
    """Supprime les copies de travail `.sdf` (appelé à la fermeture)."""
    for _, tmp in list(_snapshots.values()):
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
    _snapshots.clear()
    # Restes d'une session précédente interrompue (crash, arrêt brutal).
    try:
        for p in _SNAP_DIR.glob("*.sdf"):
            p.unlink(missing_ok=True)
    except OSError:
        pass


def sqlce_runtime_hint(root: str | None) -> str:
    """
    Message d'aide quand le runtime SQL Server Compact 3.5 (64 bits) manque :
    la Passerelle est un processus 64 bits et ne peut pas utiliser la version
    32 bits installée par défaut avec certaines versions d'ADN.
    """
    msi = None
    if root:
        cand = Path(root) / "SSCERuntime_x64-FRA.msi"
        if cand.is_file():
            msi = cand
    txt = ("Le composant Microsoft SQL Server Compact 3.5 (64 bits) n'est pas "
           "installé sur ce poste ; il est nécessaire pour lire les bases .sdf "
           "d'Analys'immo.")
    if msi:
        txt += f"\nInstallez-le en double-cliquant sur :\n{msi}"
    else:
        txt += ("\nInstallez « SSCERuntime_x64-FRA.msi » (fourni dans le "
                "dossier d'installation d'Analys'immo).")
    return txt


def sqlce_available() -> bool:
    try:
        _load_sqlce()
        return True
    except Exception:
        return False


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

    sdf_cfg = cfg.get("analysimo_sdf") or ""
    sdf_cfg_ok = bool(sdf_cfg) and Path(sdf_cfg).is_file()
    root = find_adn_root(cfg.get("adn_root", ""))
    if root is None and sdf_cfg_ok:
        # Ancien réglage : seule la base .sdf est connue. Le dossier
        # d'installation est celui qui contient « Synchro\<SDL>\ ».
        p = Path(sdf_cfg)
        root = str(p.parent.parent.parent if p.parent.parent.name.lower()
                   == "synchro" else p.parent)
    if root is None:
        raise AdnUnavailable(
            "Analys'immo (ADN) n'a pas été trouvé sur ce poste.\n"
            "Ouvrez Analys'immo puis réessayez, ou indiquez son dossier "
            "d'installation via « Configurer le logiciel de diagnostic… »."
        )

    sd = read_sd_config(root)
    candidates = _candidates(sd, cfg.get("adn_connection_string", ""))
    # La dernière connexion qui a fonctionné est réessayée en premier : la
    # passerelle se reconnecte à chaque action, inutile de rejouer toute la
    # détection (instances LocalDB qui démarrent, délais réseau…).
    last = _last_ok.get(root)
    if last:
        candidates.sort(key=lambda c: c[1] != last)
    for label, cs in candidates:
        src = SqlServerSource(cs, label)
        try:
            src.probe()
            src.root = root
            src.sd_config = sd
            _last_ok[root] = cs
            return src
        except Exception as e:
            tried.append(f"  • {label} : {_short(e)}")

    # Repli : installation d'évaluation en SQL Server Compact.
    sdf_path = Path(sdf_cfg) if sdf_cfg_ok else find_sdf(root)
    if sdf_path is not None:
        if not sqlce_available():
            raise AdnUnavailable(sqlce_runtime_hint(root))
        src = SdfSource(str(sdf_path))
        try:
            src.probe()
            src.root = root
            src.sd_config = sd
            return src
        except Exception as e:
            tried.append(f"  • base {sdf_path.name} : {_short(e)}")

    hint = ""
    if not candidates and sdf_path is None:
        hint = ("\nAucune configuration de base n'a été trouvée (sd.config "
                "absent, aucune instance LocalDB ADN). Ouvrez Analys'immo une "
                "fois sur ce poste, puis réessayez.")
    raise AdnUnavailable(
        f"Analys'immo a été trouvé dans {root}, mais aucune de ses bases "
        "n'est accessible :\n" + "\n".join(tried or ["  • aucune piste testée"])
        + hint
    )


_last_ok: dict[str, str] = {}


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
