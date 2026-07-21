# CDC 3 — Passerelle desktop

> Projet : `optimmo_bridge` (ce repo — Python, pystray + tkinter).
> Objectif : imposer une **connexion via le navigateur** (OAuth loopback + PKCE)
> avant d'utiliser la Passerelle, et **authentifier les uploads DPE** avec le JWT.

## 1. Nouveau module `auth.py`

Client OAuth « Authorization Code + PKCE » pour app native.

### 1.1 Fonctions publiques
```python
def login() -> bool          # lance le flux navigateur, stocke les jetons. True si succès.
def logout() -> None         # efface les jetons stockés.
def is_authenticated() -> bool
def get_access_token() -> str | None      # rafraîchit si nécessaire
def current_user() -> dict | None         # profil mis en cache (GET /account/user)
```

### 1.2 Déroulé de `login()`
1. Générer `code_verifier` (43–128 car. URL-safe) + `code_challenge =
   base64url(sha256(verifier))` + `state` aléatoire.
2. Démarrer un serveur HTTP local sur `127.0.0.1:0` (port éphémère libre),
   route `GET /callback`.
3. Ouvrir le navigateur (`webbrowser.open`) sur :
   ```
   {WEBAPP_URL}/passerelle/authorize?redirect_uri=http://127.0.0.1:<port>/callback
       &state=<state>&code_challenge=<challenge>&code_challenge_method=S256
   ```
4. Attendre la requête loopback (timeout ~180 s). À réception :
   - Vérifier `state`. Gérer `?error=…`.
   - Échanger le `code` : `POST {AUTH_API_URL}/desktop/token`
     `{ code, code_verifier, redirect_uri }` → `{ access_token, refresh_token }`.
   - Répondre au navigateur une page HTML « Connexion réussie, vous pouvez fermer
     cet onglet et revenir à la Passerelle ».
5. Stocker les jetons (voir §2), arrêter le serveur local, retourner `True`.

### 1.3 `get_access_token()` — refresh transparent
- Retourne l'access token stocké.
- Sur `403 { detail: "Access Token Is Expired" }` lors d'un appel API : appeler
  `POST {AUTH_API_URL}/refresh` (header `Authorization: Bearer <refresh_token>`),
  stocker les nouveaux jetons, rejouer l'appel.
- Sur `401` (refresh invalide/révoqué) : `logout()` + exiger une reconnexion.

## 2. Stockage des jetons

- **Recommandé** : `keyring` (→ Windows Credential Manager) sous le service
  `optimmo_passerelle`, clés `access_token` / `refresh_token`. Ajouter `keyring`
  à `requirements.txt`.
- **Repli** : fichier `~/.optimo_bridge/auth.json` en **lecture seule
  utilisateur** si `keyring` indisponible.
- Ne jamais logguer les jetons.

## 3. Intégration à l'UI (tray)

Fichier `tray.py` :
- **Au démarrage** (`run()` / `_post_start`) : si `not auth.is_authenticated()`,
  afficher un état « Non connecté » et **désactiver** les actions d'envoi.
- **Menu** :
  - Ajouter en tête `MenuItem("Se connecter…", …)` → thread `auth.login()` →
    au succès, notifier « Connecté : <prénom nom> » et reconstruire le menu.
  - Ajouter `MenuItem("Se déconnecter", …)` visible seulement si connecté.
  - Afficher l'utilisateur courant (désactivé) : `f"Connecté : {user['email_address']}"`.
- **Garde d'action** : `_on_send` et `_on_select` doivent d'abord vérifier
  `auth.is_authenticated()`. Sinon, proposer la connexion (au lieu d'envoyer).

## 4. Envoi authentifié des DPE

Fichier `send.py` — dans `send_dpe`, **hors mode démo** :
```python
token = auth.get_access_token()
if not token:
    raise RuntimeError("Non authentifié — connectez-vous d'abord.")
headers["Authorization"] = f"Bearer {token}"
```
- Remplacer l'actuel `x-api-key` par le `Bearer` JWT (garder `x-api-key` seulement
  si le backend l'exige encore en transition — voir [CDC 4](cdc-4-backend.md)).
- Sur réponse `401/403`, tenter un refresh via `auth` puis rejouer une fois.
- Idem pour `email_report._fetch_ecarts` (appel authentifié).

## 5. Configuration (`config.py`)

Ajouter aux `DEFAULTS` :
```python
"webapp_url":  "https://app-espace-pro.optimmo-energies.com",
"auth_api_url":"https://authentication-service-xfyprtzkyq-ew.a.run.app",
"require_auth": True,   # False = ancien comportement (démo/local sans login)
```

## 6. Dépendances

- `keyring` (stockage sécurisé). `requests` et `webbrowser` déjà présents.
- Le serveur loopback : `http.server` de la stdlib (aucune dépendance).

## 7. Sécurité

- `code_verifier` généré via `secrets.token_urlsafe`.
- Serveur loopback lié à `127.0.0.1` **uniquement** (jamais `0.0.0.0`), fermé dès
  le code reçu, timeout strict.
- Vérifier `state`. Ignorer toute requête loopback sans `state` valide.
- Ne traiter qu'**une** redirection puis arrêter le serveur.

## 8. Impact sur l'auto-update / build

- `keyring` doit être embarqué par PyInstaller (le hook contrib le gère ; vérifier
  au build). Ajouter à l'E2E un test d'import de `keyring`.
- Aucune régression attendue sur `updater.py` / `startup.py`.

## 9. Critères d'acceptation

- [ ] Au 1ᵉʳ lancement sans session : les actions d'envoi sont bloquées, un
      « Se connecter… » est proposé.
- [ ] « Se connecter » ouvre le navigateur, login web, retour auto à l'app connectée.
- [ ] Le profil (`GET /account/user`) est récupéré et affiché.
- [ ] Un upload DPE part avec `Authorization: Bearer` et réussit.
- [ ] Access token expiré → refresh transparent, upload rejoué, aucun re-login.
- [ ] Refresh révoqué (401) → l'app repasse « Non connecté ».
- [ ] Jetons stockés dans le Credential Manager (pas en clair).
- [ ] `Se déconnecter` efface les jetons et re-bloque les envois.

## 10. Estimation

Moyenne : ~1 module `auth.py` (~200 lignes) + branchements tray/send/config.
