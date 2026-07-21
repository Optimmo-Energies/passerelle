# Authentification de la Passerelle — Vue d'ensemble

> Cahier des charges : connexion de la **Passerelle desktop** (app Python en barre
> des tâches) à l'identité de l'**Espace Pro** (`app-espace-pro.optimmo-energies.com`)
> **sans réimplémenter l'authentification**.
>
> Approche retenue : **OAuth 2.0 Authorization Code + PKCE via le navigateur
> système et redirection loopback** (RFC 8252, « OAuth for Native Apps »).

## 1. Pourquoi cette approche

- L'utilisateur se connecte avec **l'écran de login web habituel** (mot de passe,
  SSO éventuel, mot de passe oublié…) — rien de tout ça n'est recodé dans l'app.
- Le **mot de passe ne transite jamais** par l'application desktop.
- La Passerelle ne reçoit qu'un couple de **jetons JWT** (les mêmes que le web) :
  `access_token` (court) + `refresh_token` (long).
- Réutilise l'endpoint `/refresh` **existant** du service d'authentification.

## 2. Écosystème existant (constaté dans le code Espace Pro)

| Service | URL | Rôle |
|---|---|---|
| Frontend Espace Pro | `https://app-espace-pro.optimmo-energies.com` | SPA vanilla JS |
| Service d'auth | `https://authentication-service-…-ew.a.run.app` (`AUTH_API_URL`) | émet/rafraîchit les JWT (FastAPI / Cloud Run) |
| Backend API | `https://api-espace-pro.optimmo-energies.com` (`API_URL`) | router + BDD, endpoints authentifiés |
| `dpe_ingest` | `https://api.optimmo-energies.com/dpe_en_cours/upload` | reçoit les DPE de la Passerelle → transfère à `dpe_analysis` |
| `dpe_analysis` | (interne) | analyse d'écarts, en aval de `dpe_ingest` |

**Contrat d'auth existant réutilisé :**

- **Login web** : `POST {AUTH_API_URL}/authenticate` — corps `username`/`password`
  en `application/x-www-form-urlencoded` → `{ access_token, refresh_token, user }`.
- **Refresh** : `POST {AUTH_API_URL}/refresh` — header
  `Authorization: Bearer <refresh_token>` → `{ access_token, refresh_token }`.
- **Access token expiré** : HTTP `403`, corps `{ "detail": "Access Token Is Expired" }`.
- **Session révoquée / refresh invalide** : HTTP `401`.
- **Profil** : `GET {API_URL}/account/user` → `{ id, first_name, last_name,
  email_address, scopes, network: { id, name, role, billing_mode }, … }`.

## 3. Flux cible (Authorization Code + PKCE, loopback)

```
Passerelle (desktop)            Navigateur système            Auth service            Frontend web
      |                               |                            |                        |
 (1) génère code_verifier +          |                            |                        |
     code_challenge (S256) + state   |                            |                        |
 (2) démarre serveur local           |                            |                        |
     http://127.0.0.1:<port>/callback|                            |                        |
 (3) ouvre le navigateur ----------->|                            |                        |
     …/passerelle/authorize?redirect_uri=…&state=…&code_challenge=…&code_challenge_method=S256
      |                               |------ charge la page ------------------------------>|
      |                               |   (4) si non connecté : login web habituel          |
      |                               |                            |<-- /authenticate ------|
      |                               |                            |--- access+refresh ---->|
      |                               |   (5) échange l'access contre un CODE court         |
      |                               |------ POST /desktop/authorize (Bearer access) ----->|
      |                               |             { code_challenge, redirect_uri }        |
      |                               |<----------------- { code } -------------------------|
      |                               |   (6) redirige vers le loopback                     |
      |<-- GET /callback?code=…&state=… (127.0.0.1) --|                                     |
 (7) vérifie state                    |                            |                        |
 (8) POST {AUTH}/desktop/token -------------------------------->  |                        |
     { code, code_verifier, redirect_uri }                        |  (9) vérifie PKCE,     |
      |                                                            |      émet les JWT      |
      |<--------------- { access_token, refresh_token } ----------|                        |
(10) stocke les jetons, ferme le serveur local,                   |                        |
     débloque les fonctionnalités                                 |                        |
(11) uploads DPE avec  Authorization: Bearer <access_token>       |    (backend valide)    |
```

## 4. Périmètre par projet (4 cahiers des charges)

| # | Projet | Ce qui change | Charge estimée |
|---|--------|---------------|----------------|
| 1 | **Service d'auth** — [cdc-1-auth-service.md](cdc-1-auth-service.md) | 2 endpoints : `/desktop/authorize` (mint code) + `/desktop/token` (échange PKCE) | Moyenne |
| 2 | **Frontend web** — [cdc-2-frontend.md](cdc-2-frontend.md) | 1 route `/passerelle/authorize` (gate login + redirection loopback) | Faible |
| 3 | **Passerelle desktop** — [cdc-3-passerelle.md](cdc-3-passerelle.md) | client OAuth loopback, stockage jetons, refresh, gating UI | Moyenne |
| 4 | **`dpe_ingest`** — [cdc-4-backend.md](cdc-4-backend.md) | valider le JWT sur l'upload DPE + propager l'identité à `dpe_analysis` | Faible |

## 5. Variante allégée (si on veut éviter de toucher au service d'auth)

Si modifier le service d'auth est trop coûteux à court terme : le **frontend**
peut, une fois l'utilisateur connecté, rediriger directement vers
`http://127.0.0.1:<port>/callback#access_token=…&refresh_token=…` (jetons dans le
**fragment** `#`, non envoyé aux serveurs). → **seuls 2 projets** changent (frontend
+ passerelle), pas le service d'auth.

- ➖ Moins sûr : les jetons apparaissent dans l'URL (historique navigateur), et un
  `refresh_token` long y transite.
- ➕ Zéro modif du service d'auth.

**Recommandation** : viser le flux PKCE complet (§3) pour un déploiement au réseau
partenaire ; la variante allégée est acceptable pour une phase de test interne.

## 6. Conventions communes

- **PKCE** : `code_verifier` = 43–128 caractères aléatoires URL-safe ;
  `code_challenge = base64url( SHA256( code_verifier ) )` ; `code_challenge_method = "S256"`.
- **redirect_uri** : uniquement **loopback** — `http://127.0.0.1:<port>/callback`
  (port éphémère choisi par la Passerelle au démarrage). `localhost` accepté aussi.
- **state** : aléatoire (anti-CSRF), vérifié au retour.
- **code** : usage unique, durée de vie **≤ 60 s**.
- Tous les échanges en **HTTPS** (sauf le loopback local, en HTTP sur 127.0.0.1).
