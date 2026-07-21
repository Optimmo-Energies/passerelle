# CDC 2 — Frontend Espace Pro

> Projet : `espace-pro/frontend` (SPA vanilla JS, Webpack). URL :
> `https://app-espace-pro.optimmo-energies.com`.
> Objectif : ajouter une route **`/passerelle/authorize`** qui sert de pont entre
> la session web de l'utilisateur et l'app desktop (redirection loopback).

## 1. Rôle de la page

Quand la Passerelle ouvre le navigateur sur cette URL :
1. S'assurer que l'utilisateur est **connecté** (sinon → login habituel, puis retour).
2. Échanger sa session contre un **code** via `POST {AUTH_API_URL}/desktop/authorize`.
3. **Rediriger** le navigateur vers le `redirect_uri` loopback avec `code` + `state`.
4. Afficher une page de confirmation « Vous pouvez revenir à la Passerelle ».

## 2. URL et paramètres entrants

```
/passerelle/authorize?redirect_uri=<loopback>&state=<opaque>&code_challenge=<...>&code_challenge_method=S256
```

- `redirect_uri` : `http://127.0.0.1:<port>/callback` (à valider, voir §5).
- `state` : chaîne opaque à **réémettre telle quelle** dans la redirection.
- `code_challenge`, `code_challenge_method` : transmis tels quels à `/desktop/authorize`.

## 3. Comportement détaillé

### 3.1 Enregistrer la route
Ajouter dans `src/index.js` la route `/passerelle/authorize` → nouveau contrôleur
`controllers/PasserelleAuthorize.js`.

### 3.2 Gate d'authentification
- Réutiliser le mécanisme existant (`Router` vérifie `optimmo_access` en
  `localStorage`).
- Si **non connecté** : rediriger vers `/connexion?next=<URL /passerelle/authorize
  encodée>` afin que, après login, l'utilisateur **revienne** sur la page authorize.
  *(Prévoir la prise en charge du paramètre `next` dans le flux de login s'il
  n'existe pas déjà.)*

### 3.3 Échange session → code
Une fois connecté, appeler le service d'auth (voir [CDC 1](cdc-1-auth-service.md)) :

```js
const res = await axiosBaseInstance.post(
  `${process.env.AUTH_API_URL}/desktop/authorize`,
  { code_challenge, code_challenge_method, redirect_uri },
  { headers: { Authorization: `Bearer ${localStorage.getItem('optimmo_access')}` } }
);
// res.data.code
```

> Si l'access token est expiré (403 « Access Token Is Expired »), l'instance axios
> authentifiée existante le rafraîchit automatiquement — préférer
> `getAxiosAPIInstance()` si l'appel passe par `API_URL`, sinon gérer le refresh
> comme dans `authenticated.js`.

### 3.4 Redirection vers le loopback
```js
const url = new URL(redirect_uri);
url.searchParams.set('code', res.data.code);
url.searchParams.set('state', state);
window.location.assign(url.toString());
```

### 3.5 Page de confirmation
La redirection vers `127.0.0.1` est interceptée par la Passerelle, qui répond une
petite page HTML (« connexion réussie, revenez à l'app »). Côté frontend, prévoir
malgré tout un fallback visuel **avant** la redirection (« Connexion de la
Passerelle en cours… ») au cas où le port local ne répondrait pas.

## 4. Cas d'erreur (UX)

| Cas | Comportement |
|---|---|
| `redirect_uri` absent/invalide | Afficher une erreur, **ne pas** rediriger. |
| `/desktop/authorize` renvoie 401 | Forcer une reconnexion (session expirée). |
| `/desktop/authorize` renvoie 4xx/5xx | Message « Impossible d'autoriser la Passerelle, réessayez ». |
| L'utilisateur annule | Rediriger le loopback avec `?error=access_denied&state=…`. |

## 5. Sécurité

- **Valider `redirect_uri`** côté frontend aussi (loopback uniquement) avant tout
  affichage/redirection, pour ne pas servir de tremplin.
- Ne jamais mettre de jeton dans l'URL de redirection (seulement le `code`).
- `state` réémis à l'identique.

## 6. Critères d'acceptation

- [ ] `/passerelle/authorize` non connecté → login → retour automatique sur la page.
- [ ] Connecté → redirection vers `127.0.0.1:<port>/callback?code=…&state=…`.
- [ ] `state` renvoyé identique à l'entrée.
- [ ] `redirect_uri` non loopback → erreur, pas de redirection.
- [ ] Annulation → `?error=access_denied`.

## 7. Estimation

Faible : 1 contrôleur + 1 route + prise en charge du paramètre `next` au login.
Aucune dépendance nouvelle.
