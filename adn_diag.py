"""
Rapport de diagnostic Analys'immo (ADN), destiné au support Optimmo.

Quand un poste n'arrive pas à lire Analys'immo, l'utilisateur lance
« Diagnostic Analys'immo… » depuis le menu : la Passerelle écrit un rapport
texte sur le Bureau et l'ouvre. Il décrit, sans aucun secret (mots de passe
masqués, aucun identifiant ADEME), tout ce qui conditionne la lecture :
installation trouvée, configuration sd.config, instances LocalDB, composants
.NET, tentatives de connexion, et un essai complet sur le dernier DPE.

Également utilisable sans interface : `PasserelleOptimmo.exe --diagnostic-adn
[fichier]` (sert à valider l'exécutable construit).
"""
import platform
import struct
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def _mask(sd: dict) -> dict:
    out = dict(sd)
    if out.get("password"):
        out["password"] = "***"
    return out


def build_report(cfg: dict, deep: bool = True) -> str:
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    def step(title: str, fn):
        t = time.monotonic()
        try:
            res = fn()
            w(f"[OK] {title} ({time.monotonic() - t:.1f} s)")
            return res
        except Exception as e:
            w(f"[KO] {title} ({time.monotonic() - t:.1f} s) : {e}")
            tb = traceback.format_exc().strip().splitlines()
            for l in tb[-6:]:
                w(f"      {l}")
            return None

    try:
        from version import __version__
    except Exception:
        __version__ = "?"
    w("DIAGNOSTIC PASSERELLE OPTIMMO — ANALYS'IMMO (ADN)")
    w(f"Date            : {datetime.now().isoformat(timespec='seconds')}")
    w(f"Passerelle      : {__version__} "
      f"({'exécutable' if getattr(sys, 'frozen', False) else 'script'})")
    w(f"Windows         : {platform.platform()}")
    w(f"Python          : {platform.python_version()} "
      f"{struct.calcsize('P') * 8} bits")
    w(f"adn_root (cfg)  : {cfg.get('adn_root') or '(auto)'}")
    w(f"analysimo_sdf   : {cfg.get('analysimo_sdf') or '-'}")
    w(f"chaîne forcée   : {'oui' if cfg.get('adn_connection_string') else 'non'}")
    w()

    import adn_db

    w("— Installation")
    for cand in adn_db.candidate_roots(cfg.get("adn_root", "")):
        if adn_db.is_adn_root(cand):
            d = Path(cand)
            w(f"  trouvé : {cand}  (ADN.exe={'oui' if (d / 'ADN.exe').is_file() else 'non'}, "
              f"sd.config={'oui' if (d / 'sd.config').is_file() else 'non'})")
    root = adn_db.find_adn_root(cfg.get("adn_root", ""))
    w(f"  retenu : {root or 'AUCUN'}")
    w()

    if root:
        w("— sd.config")
        sd = adn_db.read_sd_config(root)
        if sd:
            for k, v in _mask(sd).items():
                w(f"  {k:17}: {v}")
        else:
            w("  absent ou illisible")
        w()

    w("— Composants")
    step("pythonnet / .NET (System.Data)", adn_db._load_sqlclient)
    w(f"  SqlLocalDB.exe : {adn_db._sqllocaldb_exe() or 'non trouvé'}")
    w(f"  instances LocalDB : {', '.join(adn_db._localdb_instances()) or 'aucune'}")
    w(f"  SQL Server Compact 3.5 x64 : "
      f"{'disponible' if adn_db.sqlce_available() else 'NON disponible'}")
    w()

    w("— Connexion")
    if root:
        sd = adn_db.read_sd_config(root)
        for label, _ in adn_db._candidates(sd, cfg.get("adn_connection_string", "")):
            w(f"  piste : {label}")
    src = step("connexion aux bases", lambda: adn_db.connect(cfg))
    if src is None:
        return "\n".join(lines)
    w(f"  source : {src.kind} — {src.label}")
    if src.kind == "sqlserver":
        for logical in (adn_db.DB_DIAG, adn_db.DB_DPE, adn_db.DB_RG):
            w(f"  base {logical:17}→ {src.resolve_db(logical)}")
    w()

    import adn
    w("— Dossiers DPE")
    dossiers = step("liste des dossiers DPE",
                    lambda: adn.list_dossiers(src, limit=10)) or []
    for d in dossiers[:10]:
        w(f"  {d.get('idDossier')} | {d.get('reference')} | maj {d.get('dateMaj')}")
    if not dossiers or not deep:
        return "\n".join(lines)
    w()

    d = dossiers[0]
    w(f"— Essai complet sur « {d.get('reference')} »")
    missions = step("missions DPE", lambda: adn.get_dpe_missions(src, d["idDossier"])) or []
    if not missions:
        return "\n".join(lines)
    m = missions[0]
    w(f"  mission {m.get('idMission')} catégorie {m.get('idCategorieMission')} "
      f"type {m.get('idTypeMission')}")
    s = step("résumé", lambda: adn.parse_dpe_summary(src, d, m))
    if s:
        w(f"  classe {s.get('classe_energie')}/{s.get('classe_co2')} "
          f"conso {s.get('consommation')} calcul={s.get('calcul_effectue')} "
          f"ADEME={s.get('numero_ademe')}")
    p = step("extraction de la saisie", lambda: adn.read_dpe(src, d, m))
    if p:
        w(f"  volumétrie : {p['meta'].get('volumetrie')}")

    def _ademe():
        import ademe_adn
        nom, data, rapport = ademe_adn.build(src, d, m, cfg)
        return nom, len(data), rapport

    a = step("génération du XML ADEME", _ademe)
    if a:
        w(f"  {a[0]} ({a[1]} octets)")
        rap = a[2] or {}
        for k in ("erreurs_xsd", "avertissements", "elements"):
            if k in rap:
                w(f"  {k} : {str(rap[k])[:400]}")
    off = adn.official_xml(src, d)
    w(f"  XML officiel Analys'immo : {f'{off[0]} ({off[2]})' if off else 'aucun'}")
    return "\n".join(lines)


def write_report(cfg: dict, dest: str | None = None) -> Path:
    try:
        text = build_report(cfg)
    except Exception:
        text = "Diagnostic interrompu :\n" + traceback.format_exc()
    if dest:
        path = Path(dest)
    else:
        desktop = Path.home() / "Desktop"
        if not desktop.is_dir():
            desktop = Path.home()
        path = desktop / f"Diagnostic_Passerelle_AnalysImmo_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(text, encoding="utf-8-sig")
    return path
