# CDC 1 — Service d'authentification

> Projet : `authentication-service` (FastAPI / Cloud Run, `AUTH_API_URL`).
> Objectif : permettre à une **app desktop** d'obtenir les JWT d'un utilisateur
> déjà connecté sur le web, via un **code d'autorisation à usage unique + PKCE**.

## 1. Contexte

Le service émet déjà des JWT via `POST /authenticate` (login web) et les rafraîchit
via `POST /refresh`. On **ne touche pas** à ces endpoints. On ajoute un pont pour
les clients natifs qui ne peuvent pas stocker de secret : le flux **Authorization
Code + PKCE** (RFC 7636 / RFC 8252).

On ajoute **2 endpoints** et **un stockage temporaire de codes**.

## 2. Endpoint A — `POST /desktop/authorize`

Appelé **depuis le frontend web** (donc par un utilisateur déjà authentifié) pour
transformer sa session en un **code court** remis à l'app desktop.

### Requête
```
POST {AUTH_API_URL}/desktop/authorize
Authorization: Bearer <access_token de l'utilisateur connecté>
Content-Type: application/json

{
  "code_challenge": "<base64url(sha256(code_verifier))>",
  "code_challenge_method": "S256",
  "redirect_uri": "http://127.0.0.1:52731/callback"
}
```

### Traitement
1. **Valider l'access token** (middleware d'auth existant) → identifie `user_id`.
   Si invalide/expiré → `401`.
2. **Valider `redirect_uri`** : doit matcher `^http://(127\.0\.0\.1|localhost):\d{1,5}/callback$`.
   Sinon → `400 { "detail": "Invalid redirect_uri" }`. *(Sécurité clé : n'autoriser
   que le loopback interdit l'exfiltration du code vers un domaine tiers.)*
3. **Valider `code_challenge_method` == "S256"** (rejeter `plain`).
4. **Générer** `code` = 32+ octets aléatoires URL-safe.
5. **Stocker** l'entrée : `{ code → (user_id, code_challenge, redirect_uri,
   expires_at = now+60s, used=false) }` (voir §4).
6. Répondre :

```
200 OK
{ "code": "<code>", "expires_in": 60 }
```

## 3. Endpoint B — `POST /desktop/token`

Appelé **depuis l'app desktop** (client public, sans secret) pour échanger le code
contre les vrais JWT.

### Requête
```
POST {AUTH_API_URL}/desktop/token
Content-Type: application/json

{
  "code": "<code reçu via le loopback>",
  "code_verifier": "<le verifier d'origine>",
  "redirect_uri": "http://127.0.0.1:52731/callback"
}
```

### Traitement
1. **Récupérer** l'entrée par `code`. Absente → `400 { "detail": "Invalid code" }`.
2. **Vérifier** : non `used`, non expirée (`now < expires_at`), `redirect_uri`
   identique à celui du `/desktop/authorize`. Sinon → `400`.
3. **Vérifier PKCE** : `base64url(sha256(code_verifier)) == code_challenge`.
   Échec → `400 { "detail": "PKCE verification failed" }`.
4. **Marquer** l'entrée `used=true` (usage unique, atomique — voir §4).
5. **Émettre les JWT** pour `user_id` avec **exactement la même logique que
   `/authenticate`** (même payload, mêmes durées, même clé de signature).
6. Répondre :

```
200 OK
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer"
}
```

> L'app desktop utilisera ensuite `/refresh` **existant** pour renouveler l'access
> token — aucune modification nécessaire de cet endpoint.

## 4. Stockage des codes

- Table/collection `desktop_auth_codes` (ou Redis avec TTL 60 s si dispo).
- Champs : `code` (indexé, unique), `user_id`, `code_challenge`, `redirect_uri`,
  `expires_at`, `used`.
- **Usage unique atomique** : l'invalidation (`used=true`) et la lecture doivent
  être une opération atomique (transaction, ou `UPDATE … WHERE used=false
  RETURNING …`, ou `GETDEL` Redis) pour éviter le rejeu concurrent.
- Purge des entrées expirées (job/TTL).

## 5. Sécurité (exigences)

- `redirect_uri` **loopback uniquement** (regex stricte ci-dessus).
- `code` : ≥ 256 bits d'entropie, TTL ≤ 60 s, **usage unique**.
- PKCE **S256 obligatoire** ; refuser `plain`.
- Aucune donnée sensible dans les logs (ni `code`, ni jetons).
- Rate-limiting sur `/desktop/token` (anti-bruteforce de codes).
- CORS : `/desktop/authorize` doit accepter l'origine du frontend
  (`https://app-espace-pro.optimmo-energies.com`). `/desktop/token` est appelé par
  l'app desktop (pas de navigateur) → CORS indifférent.

## 6. Cas d'erreur normalisés

| Situation | HTTP | `detail` |
|---|---|---|
| Access token invalide/expiré (`/desktop/authorize`) | 401 | (au choix, cohérent avec l'existant) |
| `redirect_uri` non loopback | 400 | `Invalid redirect_uri` |
| Méthode PKCE ≠ S256 | 400 | `Unsupported code_challenge_method` |
| Code inconnu / expiré / déjà utilisé | 400 | `Invalid code` |
| Échec vérification PKCE | 400 | `PKCE verification failed` |

## 7. Critères d'acceptation

- [ ] Un access token valide + un `code_challenge` renvoient un `code` (200).
- [ ] `redirect_uri` non-loopback rejeté (400).
- [ ] `code` échangé avec le bon `code_verifier` renvoie access+refresh valides.
- [ ] Le même `code` réutilisé une 2ᵉ fois → 400 (usage unique).
- [ ] Un mauvais `code_verifier` → 400 (PKCE).
- [ ] Code utilisé après 61 s → 400 (expiration).
- [ ] Les JWT émis passent la validation des endpoints backend existants.

## 8. Hors périmètre

- Pas de changement à `/authenticate` ni `/refresh`.
- Pas de gestion de consentement applicatif (l'app est first-party Optimmo).
