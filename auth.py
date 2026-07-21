"""
Authentification de la Passerelle via l'Espace Pro.

Flux OAuth 2.0 « Authorization Code + PKCE » avec redirection loopback
(RFC 8252, apps natives). Le mot de passe ne transite JAMAIS par l'app :
l'utilisateur se connecte dans son navigateur sur la webapp, qui renvoie un
code court échangé ici contre des JWT.

Voir docs/passerelle-auth/cdc-3-passerelle.md.

Tant que `require_auth` est False (défaut), ce module n'est pas sollicité par
le reste de l'app : rien ne change tant que les endpoints web ne sont pas livrés.
"""
import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

_TOKENS_FILE = Path.home() / ".optimo_bridge" / "auth.json"
_KEYRING_SERVICE = "optimmo_passerelle"
_DEFAULT_ESPACE_PRO_API = "https://api-espace-pro.optimmo-energies.com"

_user_cache = {"data": None}


class ReauthRequired(Exception):
    """
    La session Espace Pro a expiré ou été révoquée et n'a pas pu être
    renouvelée : l'utilisateur doit se reconnecter (flux navigateur).
    Levée par `send.send_dpe` pour que l'IHM propose une reconnexion.
    """

_SUCCESS_HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Optimmo Passerelle</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#0A1628;color:#fff;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<div style="font-size:48px;color:#009B6C">&#10004;</div>
<h2>Connexion réussie</h2>
<p>Vous pouvez fermer cet onglet et revenir à la Passerelle Optimmo.</p>
</div></body></html>"""


# ── Stockage des jetons (Credential Manager si dispo, sinon fichier) ──────────
def _store_tokens(access: str, refresh: str) -> None:
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, "access_token", access)
        keyring.set_password(_KEYRING_SERVICE, "refresh_token", refresh)
        return
    except Exception:
        pass
    _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_FILE.write_text(
        json.dumps({"access_token": access, "refresh_token": refresh}),
        encoding="utf-8",
    )


def _load_tokens() -> tuple[str | None, str | None]:
    try:
        import keyring
        access = keyring.get_password(_KEYRING_SERVICE, "access_token")
        refresh = keyring.get_password(_KEYRING_SERVICE, "refresh_token")
        if access and refresh:
            return access, refresh
    except Exception:
        pass
    if _TOKENS_FILE.exists():
        try:
            data = json.loads(_TOKENS_FILE.read_text(encoding="utf-8"))
            return data.get("access_token"), data.get("refresh_token")
        except Exception:
            pass
    return None, None


def _clear_tokens() -> None:
    try:
        import keyring
        for key in ("access_token", "refresh_token"):
            try:
                keyring.delete_password(_KEYRING_SERVICE, key)
            except Exception:
                pass
    except Exception:
        pass
    _TOKENS_FILE.unlink(missing_ok=True)
    _user_cache["data"] = None


# ── PKCE ──────────────────────────────────────────────────────────────────────
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ── Serveur loopback ──────────────────────────────────────────────────────────
class _CallbackServer(http.server.HTTPServer):
    auth_result: dict | None = None
    auth_event: threading.Event


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode("utf-8"))
        self.server.auth_event.set()

    def log_message(self, *args):  # silence
        pass


# ── API publique ──────────────────────────────────────────────────────────────
def is_authenticated() -> bool:
    access, refresh = _load_tokens()
    return bool(access and refresh)


def get_access_token() -> str | None:
    access, _ = _load_tokens()
    return access


def _token_exp(jwt: str) -> int | None:
    """Timestamp d'expiration (claim `exp`) d'un JWT, sans vérif de signature."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


def valid_access_token(cfg: dict, skew: int = 30) -> str | None:
    """
    Renvoie un access token exploitable pour un appel API :
      - encore valide (marge `skew` s) → tel quel ;
      - expiré → tente un refresh silencieux et renvoie le nouveau jeton ;
      - aucune session ou refresh KO → None (reconnexion nécessaire).
    Permet de rafraîchir *avant* l'appel plutôt que de subir un 403.
    """
    access, _ = _load_tokens()
    if not access:
        return None
    exp = _token_exp(access)
    if exp is None or time.time() < exp - skew:
        # exp illisible : on laisse le serveur trancher (403 → refresh réactif).
        return access
    return refresh(cfg)


def logout() -> None:
    _clear_tokens()


def cached_user() -> dict | None:
    """Profil déjà en cache, sans appel réseau (pour l'UI)."""
    return _user_cache["data"]


def refresh(cfg: dict) -> str | None:
    """Renouvelle l'access token via le refresh token. None si échec (session révoquée)."""
    _, refresh_token = _load_tokens()
    if not refresh_token:
        return None
    try:
        resp = requests.post(
            f"{cfg['auth_api_url'].rstrip('/')}/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"},
            timeout=20,
        )
        if resp.status_code == 401:
            _clear_tokens()
            return None
        resp.raise_for_status()
        data = resp.json()
        _store_tokens(data["access_token"], data["refresh_token"])
        return data["access_token"]
    except Exception:
        return None


def login(cfg: dict, timeout: int = 180) -> bool:
    """
    Lance le flux navigateur (loopback + PKCE). Bloquant jusqu'au retour ou
    au timeout. Stocke les jetons et renvoie True en cas de succès.
    """
    auth_url = cfg["auth_api_url"].rstrip("/")
    webapp = cfg["webapp_url"].rstrip("/")
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)

    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.auth_result = None
    server.auth_event = threading.Event()
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    query = urllib.parse.urlencode({
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(f"{webapp}/passerelle/authorize?{query}")
        got = server.auth_event.wait(timeout)
    finally:
        server.shutdown()
        server.server_close()

    if not got:
        return False
    result = server.auth_result or {}
    if result.get("error") or result.get("state") != state or not result.get("code"):
        return False

    try:
        resp = requests.post(
            f"{auth_url}/desktop/token",
            json={
                "code": result["code"],
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        _store_tokens(data["access_token"], data["refresh_token"])
        _user_cache["data"] = None
        return True
    except Exception:
        return False


def current_user(cfg: dict, force: bool = False) -> dict | None:
    """Profil utilisateur (GET /account/user), mis en cache. None si non connecté."""
    if _user_cache["data"] and not force:
        return _user_cache["data"]
    base = cfg.get("espace_pro_api_url", _DEFAULT_ESPACE_PRO_API).rstrip("/")
    token = get_access_token()
    if not token:
        return None

    def _get(tok: str):
        return requests.get(
            f"{base}/account/user",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=15,
        )

    try:
        resp = _get(token)
        if resp.status_code == 403 and "Access Token Is Expired" in resp.text:
            tok = refresh(cfg)
            if not tok:
                return None
            resp = _get(tok)
        if resp.status_code == 401:
            _clear_tokens()
            return None
        if resp.ok:
            _user_cache["data"] = resp.json()
            return _user_cache["data"]
    except Exception:
        return None
    return None
