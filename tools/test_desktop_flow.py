"""
Test E2E du flux d'authentification desktop, SANS navigateur.

Reproduit exactement ce que fait le frontend `/passerelle/authorize`, puis ce
que fait la Passerelle (`auth.py`), puis un upload vers `dpe_ingest` :

  1. POST /authenticate            -> jeton de session web (comme un login web)
  2. POST /desktop/authorize       -> code court (rôle du frontend)
  3. POST /desktop/token (+PKCE)   -> access/refresh tokens desktop
  4. décode l'access token         -> vérifie network_id / role / technicien_id
  5. POST /dpe_en_cours/upload     -> vérifie que dpe_ingest accepte le token
                                      (== secret partagé OK)

Le mot de passe est demandé en interactif (getpass) : il ne transite ni par la
ligne de commande ni par l'historique shell.

Usage :
    python tools/test_desktop_flow.py \
        --auth https://authentication-service-dev-xfyprtzkyq-ew.a.run.app \
        --ingest https://dpe-ingest-xfyprtzkyq-ew.a.run.app
"""
import argparse
import base64
import getpass
import hashlib
import io
import json
import secrets
import zipfile

import requests


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _decode_claims(jwt: str) -> dict:
    """Décode le payload d'un JWT sans vérifier la signature (inspection seule)."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _ok(msg: str):
    print(f"  \033[92m[OK]\033[0m {msg}")


def _ko(msg: str):
    print(f"  \033[91m[KO]\033[0m {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", required=True, help="URL de base du service d'auth")
    ap.add_argument("--ingest", required=True, help="URL de base de dpe_ingest")
    ap.add_argument("--redirect", default="http://127.0.0.1:53682/callback",
                    help="redirect_uri loopback (doit finir par /callback)")
    args = ap.parse_args()
    auth = args.auth.rstrip("/")
    ingest = args.ingest.rstrip("/")

    email = input("email (compte du même environnement que l'auth) : ").strip()
    password = getpass.getpass("mot de passe : ")

    # 1) Login web ------------------------------------------------------------
    print("\n1) POST /authenticate (session web)")
    r = requests.post(
        f"{auth}/authenticate",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": email, "password": password},
        timeout=30,
    )
    if not r.ok:
        _ko(f"login échoué HTTP {r.status_code} : {r.text[:200]}")
        return 1
    web_access = r.json().get("access_token")
    if not web_access:
        _ko(f"pas d'access_token dans la réponse : {r.text[:200]}")
        return 1
    _ok("session web obtenue")
    web_claims = _decode_claims(web_access)
    print(f"     claims web : network_id={web_claims.get('network_id')} "
          f"role={web_claims.get('role')} technicien_id={web_claims.get('technicien_id')}")

    # 2) /desktop/authorize (rôle du frontend) --------------------------------
    print("\n2) POST /desktop/authorize (échange session -> code)")
    verifier, challenge = _make_pkce()
    r = requests.post(
        f"{auth}/desktop/authorize",
        headers={"Authorization": f"Bearer {web_access}"},
        json={"code_challenge": challenge, "code_challenge_method": "S256",
              "redirect_uri": args.redirect},
        timeout=30,
    )
    if not r.ok:
        _ko(f"authorize échoué HTTP {r.status_code} : {r.text[:200]}")
        return 1
    code = r.json().get("code")
    _ok(f"code obtenu (expire_in={r.json().get('expires_in')}s)")

    # 3) /desktop/token (ce que fait auth.py de la Passerelle) ----------------
    print("\n3) POST /desktop/token (code + PKCE verifier -> tokens desktop)")
    r = requests.post(
        f"{auth}/desktop/token",
        json={"code": code, "code_verifier": verifier, "redirect_uri": args.redirect},
        timeout=30,
    )
    if not r.ok:
        _ko(f"token échoué HTTP {r.status_code} : {r.text[:200]}")
        return 1
    desktop = r.json()
    desktop_access = desktop.get("access_token")
    if not desktop_access or not desktop.get("refresh_token"):
        _ko(f"réponse incomplète : {list(desktop.keys())}")
        return 1
    _ok("access_token + refresh_token desktop obtenus")

    # 3b) code rejoué -> doit échouer (single-use) ----------------------------
    r2 = requests.post(
        f"{auth}/desktop/token",
        json={"code": code, "code_verifier": verifier, "redirect_uri": args.redirect},
        timeout=30,
    )
    (_ok if r2.status_code == 400 else _ko)(
        f"rejeu du code -> HTTP {r2.status_code} (attendu 400, single-use)")

    # 4) claims du token desktop == token web ---------------------------------
    print("\n4) Claims du token desktop")
    d = _decode_claims(desktop_access)
    for claim in ("network_id", "role", "technicien_id"):
        web_v, dsk_v = web_claims.get(claim), d.get(claim)
        (_ok if web_v == dsk_v else _ko)(
            f"{claim}: desktop={dsk_v} web={web_v}")

    # 5) upload vers dpe_ingest -> secret partagé -----------------------------
    print("\n5) POST /dpe_en_cours/upload (valide le secret partagé)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("DPE_ADEME/test.xml", "<dpe/>")
        z.writestr("XML/Table_Z_DPE_2020_General.xml", "<t/>")
    r = requests.post(
        f"{ingest}/dpe_en_cours/upload",
        headers={"Authorization": f"Bearer {desktop_access}"},
        files={"dpe_zip": ("t.zip", buf.getvalue(), "application/zip")},
        data={"summary": json.dumps({"dossier": "TEST", "xml_ademe_joint": True,
                                     "xml_ademe_source": "reconstruit"})},
        timeout=60,
    )
    print(f"     HTTP {r.status_code} : {r.text[:300]}")
    if r.status_code in (200, 502):
        _ok("token ACCEPTÉ par dpe_ingest -> secret partagé OK"
            + (" (502 = transfert dpe_analysis KO mais auth OK)"
               if r.status_code == 502 else ""))
        return 0
    if r.status_code == 401:
        _ko("401 -> SECRET MISMATCH : dpe_ingest valide avec la mauvaise clé. "
            "Corriger le mapping Secret Manager (AUTH_JWT_SECRET=JWT_ACCESS_SECRET:latest).")
        return 1
    if r.status_code == 403:
        _ko("403 Forbidden -> secret OK mais rôle non autorisé "
            "(DPE_UPLOAD_ALLOWED_ROLES). Réglage d'accès, pas un bug de secret.")
        return 1
    _ko("code inattendu, voir le corps ci-dessus")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
