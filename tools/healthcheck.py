"""
Santé du déploiement du flux d'authentification (sans identifiants).

Vérifie, sur l'environnement visé :
  - l'auth expose /desktop/authorize + /desktop/token (version 1.5.0+) ;
  - dpe_ingest protège /dpe_en_cours/upload (401 sans/avec token invalide,
    et pas 404 = route déployée).

Ne teste PAS le secret partagé (ça nécessite un vrai login → tools/test_desktop_flow.py).

Usage (prod) :
    python tools/healthcheck.py \
        --auth https://authentication-service-xfyprtzkyq-ew.a.run.app \
        --ingest https://dpe-ingest-xfyprtzkyq-ew.a.run.app
"""
import argparse

import requests


def _line(ok: bool, msg: str):
    print(f"  {'[OK]' if ok else '[KO]'} {msg}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", required=True)
    ap.add_argument("--ingest", required=True)
    args = ap.parse_args()
    auth, ingest = args.auth.rstrip("/"), args.ingest.rstrip("/")
    all_ok = True

    print("AUTH")
    try:
        d = requests.get(f"{auth}/openapi.json", timeout=45).json()
        paths = d.get("paths", {})
        all_ok &= _line(d["info"]["version"] >= "1.5.0",
                        f"version = {d['info']['version']} (attendu >= 1.5.0)")
        all_ok &= _line("/desktop/authorize" in paths, "route /desktop/authorize")
        all_ok &= _line("/desktop/token" in paths, "route /desktop/token")
    except Exception as e:
        all_ok &= _line(False, f"openapi injoignable : {e}")

    print("DPE_INGEST")
    try:
        r = requests.post(f"{ingest}/dpe_en_cours/upload",
                          headers={"Authorization": "Bearer not.a.jwt"},
                          files={"dpe_zip": ("t.zip", b"PK", "application/zip")},
                          data={"summary": "{}"}, timeout=45)
        all_ok &= _line(r.status_code != 404,
                        f"route /dpe_en_cours/upload déployée (HTTP {r.status_code}, pas 404)")
        all_ok &= _line(r.status_code == 401,
                        f"gate JWT active (HTTP {r.status_code}, attendu 401 sur token invalide)")
    except Exception as e:
        all_ok &= _line(False, f"upload injoignable : {e}")

    print("\n" + ("=> TOUT VERT : lance tools/test_desktop_flow.py pour valider le secret."
                  if all_ok else "=> KO : voir les [KO] ci-dessus."))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
