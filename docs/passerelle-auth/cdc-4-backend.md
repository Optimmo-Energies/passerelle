# CDC 4 — Service `dpe_ingest`

> Projet : **`dpe_ingest`** — service qui **reçoit les DPE de la Passerelle**
> (`https://api.optimmo-energies.com/dpe_en_cours/upload`) puis les **transfère à
> `dpe_analysis`**.
> Objectif : **exiger et valider le JWT** de l'Espace Pro sur l'endpoint
> d'ingestion, et **propager l'identité** de l'auteur jusqu'à `dpe_analysis`.

## 1. Contexte

`dpe_ingest` est un service distinct du backend Espace Pro (`api-espace-pro`) et du
service d'auth. Il est le **point d'entrée** des DPE envoyés par la Passerelle,
qu'il relaie ensuite à `dpe_analysis` (recherche d'écarts).

Aujourd'hui la Passerelle poste le DPE avec un `x-api-key` partagé (ou rien). On
remplace ce mécanisme par le **JWT utilisateur** émis par le service d'auth (le
même que celui validé par `api-espace-pro` sur `GET /account/user`). Objectif :
savoir **qui** (utilisateur, réseau/bureau, technicien) a transmis chaque DPE, et
couper l'accès anonyme.

> ⚠️ **Point clé d'intégration** : `dpe_ingest` doit valider les JWT avec **la même
> clé/algorithme** que le service d'auth. Deux options :
> - **Validation locale** : partager le secret HMAC (ou la clé publique / le JWKS)
>   et l'algorithme de signature, et reproduire la vérification (signature +
>   expiration + claims). C'est le plus simple si l'auth signe en asymétrique
>   (RS256) → `dpe_ingest` n'a besoin que de la **clé publique**.
> - **Introspection distante** : appeler un endpoint du service d'auth pour valider
>   le token (plus couplé, latence réseau). Éviter si possible.
>
> Recommandation : validation **locale** via clé publique/JWKS partagé.

## 2. Périmètre

### 2.1 Sécuriser l'endpoint d'upload
- Endpoint : `POST /dpe_en_cours/upload` (multipart : `dpe_zip` + `summary`).
- Exiger l'en-tête `Authorization: Bearer <access_token>`.
- **Réutiliser le middleware/dependency de validation JWT existant** (celui qui
  protège `/account/user`, etc.) :
  - Signature + expiration valides → continuer, sinon `403`
    `{ "detail": "Access Token Is Expired" }` (expiré) ou `401` (invalide/révoqué),
    **cohérent avec le contrat existant** pour que le refresh automatique de la
    Passerelle fonctionne.
  - Extraire `user_id` (+ `network_id`, `role`, scopes) du token.

### 2.2 Contrôle d'accès
- Vérifier que l'utilisateur a le **droit** de soumettre un DPE (scope/rôle à
  définir — ex. tout compte technicien d'un réseau actif). Sinon `403`.

### 2.3 Traçabilité / propagation vers `dpe_analysis`
- Associer chaque DPE reçu à `user_id` + `network_id` (+ `technicien` si pertinent)
  et horodatage.
- **Propager cette identité à `dpe_analysis`** lors du transfert (dans le payload
  ou en métadonnées), pour que l'analyse d'écarts soit rattachée au bon
  utilisateur/réseau. Ne pas relayer le JWT brut à `dpe_analysis` : transmettre les
  identifiants extraits (ou un jeton de service interne dédié).
- Permet le futur système de droits « poupée russe » (siège / bureau / technicien).

## 3. Compatibilité & transition

- **Phase transitoire** (optionnelle) : accepter `Authorization: Bearer` **ou**
  l'ancien `x-api-key` pendant une fenêtre de migration, avec un log d'usage de la
  clé legacy, puis retirer `x-api-key`.
- Coordonner la bascule avec la version de Passerelle qui envoie le Bearer
  (voir [CDC 3](cdc-3-passerelle.md) §4).

## 4. Réponses attendues

| Cas | HTTP | Corps |
|---|---|---|
| Upload accepté | 200/201 | `{ "status": "...", "id": "..." }` |
| Token expiré | 403 | `{ "detail": "Access Token Is Expired" }` |
| Token invalide / révoqué | 401 | `{ "detail": "..." }` |
| Droits insuffisants | 403 | `{ "detail": "Forbidden" }` |
| ZIP/summary invalide | 422/400 | `{ "detail": "..." }` |

> Le code `403 "Access Token Is Expired"` est **impératif** (mot pour mot) : c'est
> le signal que le client utilise pour déclencher le refresh puis rejouer.

## 5. CORS

- L'upload est appelé par l'app desktop (pas un navigateur) → CORS non requis pour
  cet endpoint. Ne pas relâcher la politique CORS globale pour autant.

## 6. Sécurité

- Ne jamais logguer le token ni les données personnelles du DPE en clair.
- Valider la taille/typologie du ZIP (déjà `application/zip`).
- Rate-limiting raisonnable par utilisateur.

## 7. Critères d'acceptation

- [ ] Upload sans `Authorization` → 401/403 (plus d'upload anonyme).
- [ ] Upload avec JWT valide → 200 et DPE associé à `user_id`/`network_id` en BDD.
- [ ] Token expiré → 403 `Access Token Is Expired` (déclenche le refresh client).
- [ ] Token d'un autre réseau / sans droit → 403.
- [ ] (Si transition) `x-api-key` legacy encore accepté puis retirable par config.

## 8. Estimation

Faible **si** le middleware JWT existe déjà (réutilisation) : surtout brancher la
dependency d'auth sur l'endpoint d'upload + persister l'auteur. Plus élevé si
l'ingestion est un service séparé qui ne sait pas encore valider ces JWT.
