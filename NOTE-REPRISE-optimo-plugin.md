# Note de reprise — Plugin Optimo / ADN (Analys'immo)

> Rédigée le 2026-06-23. Source analysée : `C:\Users\gabga\Downloads\optimo-plugin\`
> (le dossier parent contient le vrai code ; le sous-dossier `optimo-plugin\optimo-plugin\` n'est
> qu'une extraction vide à ignorer/supprimer).
> Hypothèse validée par Gabriel : **ADN = Analys'immo** (même logiciel cible), donc le mapping
> base → XML documenté ici est directement réutilisable.

---

## 1. À quoi sert ce projet

Application **Windows desktop .NET 8** résidente en **barre des tâches** (system tray) qui :

1. détecte le logiciel **ADN** installé sur le poste (`ADN.exe`, en priorité dans `C:\ADN`) ;
2. identifie le type de base locale (LocalDB / SQL Compact / mixte) à partir de la config d'ADN ;
3. ouvre la base locale **en lecture seule** ;
4. à la demande (clic sur l'icône + choix d'une référence dossier), **reconstruit le fichier XML DPE officiel** à partir du contenu de cette base ;
5. écrit le XML reconstruit (ou un rapport d'erreur) **sur le Bureau**.

Le tout sans jamais intercepter ni bloquer l'utilisateur dans ADN.

---

## 2. Stack & build

| Élément | Valeur |
|---|---|
| Framework | .NET 8 (WinForms pour le tray) |
| SDK épinglé | `8.0.420` (`global.json`, rollForward latestPatch) |
| Langage | C# 12, `Nullable` + `ImplicitUsings` activés (`Directory.Build.props`) |
| Accès SQL | `Microsoft.Data.SqlClient` (LocalDB) |
| Solution | `Optimo.TrayApp.sln` |

**Commandes :**
```powershell
dotnet restore .\Optimo.TrayApp.sln
dotnet build   .\Optimo.TrayApp.sln -c Release
dotnet run     --project .\src\Optimo.TrayApp\Optimo.TrayApp.csproj
dotnet test    .\Optimo.TrayApp.sln
```

### ✅ Phase 0 réalisée le 2026-06-23 (build + tests verts)

Le SDK .NET 8 n'était pas installé. Installé **par-utilisateur, sans admin** via `dotnet-install.ps1`
dans `C:\Users\gabga\.dotnet` (SDK **8.0.422**). Le runtime 8.0.28 (+ WindowsDesktop) était déjà là.

> Comme le SDK n'est pas dans le PATH système, préfixer les commandes :
> ```powershell
> $env:PATH="C:\Users\gabga\.dotnet;$env:PATH"; $env:DOTNET_ROOT="C:\Users\gabga\.dotnet"
> & "C:\Users\gabga\.dotnet\dotnet.exe" build .\Optimo.TrayApp.sln -c Release
> ```

Résultats :
- **`dotnet restore`** : OK (5 projets).
- **`dotnet build -c Release`** : les 4 projets applicatifs **compilent (0 erreur)** ; ~34 warnings nullable (CS8602/CS8604) dans `LocalDbDpeXmlReconstructionService.cs`, non bloquants.
- **`dotnet test`** : **11/11 tests réussis** après correction d'un test obsolète.

**Correctif appliqué** (`tests/Optimo.Tests/DpeXmlDocumentBuilderTests.cs`) : le fixture était écrit
pour une version antérieure du record `DpeXmlReconstructionData` ; 4 paramètres ajoutés depuis
n'étaient pas fournis. Ajoutés : `VersionId` (pos. 2, → `enum_version_id`, non asserté),
`Geolocation` = null (pos. 13), `ConsentForm` = null (pos. 14), `Justifications` = [] (pos. 19) ;
les 6 derniers arguments ont été nommés explicitement pour éviter toute future ambiguïté de position.
→ **Leçon** : les fixtures de test ne sont pas tenues à jour quand les records du domaine évoluent ;
à surveiller à chaque ajout de champ.

---

## 3. Architecture (Clean Architecture)

```
src/
  Optimo.Domain/          Modèles métier (records, enums) — aucune dépendance
  Optimo.Application/     Contrats (interfaces), orchestration, validation, builder XML
  Optimo.Infrastructure/  Implémentations : UI Automation, accès LocalDB, détection, tray, démarrage
  Optimo.TrayApp/         Bootstrap WinForms, icône tray, fenêtres, cycle de vie
tests/Optimo.Tests/       Tests unitaires sur la logique non-UI
scratch/SchemaProbe/      Petit utilitaire jetable pour sonder le schéma SQL
docs/                     Documentation de rétro-ingénierie (voir §6) — la vraie valeur
```

**Fichiers clés :**
- `src/Optimo.TrayApp/TrayApplicationContext.cs` — chef d'orchestre du flux de démarrage et de l'action utilisateur.
- `src/Optimo.Infrastructure/Discovery/LocalDbDpeXmlReconstructionService.cs` — **273 Ko**, ~30 requêtes SQL sur les 3 bases ADN, construit les « snapshots ».
- `src/Optimo.Application/Services/DpeXmlDocumentBuilder.cs` — **59 Ko**, assemble le XML à partir des snapshots.
- `src/Optimo.Domain/Models/DpeXmlReconstructionData.cs` — **17 Ko**, tous les records intermédiaires.
- `src/Optimo.Infrastructure/Discovery/WindowsDatabaseDetectionService.cs` — détection du type de base via `sd.config`.
- `src/Optimo.Infrastructure/Discovery/WindowsExecutableDiscoveryService.cs` — recherche de `ADN.exe`.

---

## 4. Le flux de bout en bout

Piloté par `TrayApplicationContext`, avec une fenêtre de statut affichant 5 étapes :

1. **Monitoring** — démarrage du moteur de surveillance (UI Automation, optionnel).
2. **ExecutableSearch** — trouve `ADN.exe` (prioritairement `C:\ADN` ; balayage des disques fixes seulement si `SearchFixedDrivesIfNotFound = true`).
3. **DatabaseDetection** — inspecte le dossier d'ADN + `sd.config` → LocalDB / SQL Compact / mixte / inconnu.
4. **LocalDbInspection** — connexion read-only à l'instance LocalDB, liste bases + tables.
5. **DossierReferences** — charge la liste des références dossier pour la combo.

Puis, sur clic icône → fenêtre d'action → choix référence → bouton envoyer :
`LocalDbDpeXmlReconstructionService.ReconstructAsync()` → snapshots SQL → `DpeXmlDocumentBuilder` → XML.
Sortie : `ADN_DPE_Reconstruit_<timestamp>.xml` sur le Bureau (ou `ADN_DPE_Erreur_<timestamp>.txt`).

---

## 5. Les 3 bases ADN exploitées (LocalDB)

| Base | Rôle |
|---|---|
| `ADN_DIAG` | Dossier, Mission, interlocuteurs (propriétaire / donneur d'ordre / payeur), adresse + `infoBAN` |
| `ADN_DIAG_DPE2012` | Cœur DPE : `XDPEdossierDPE`, `XDPEdetailInformationLogement`, `XDPEsaisieLot` (pivot), familles `XDPEdetailSaisieEnv*`, générateurs, ventilation, clim, `XDPEsortieMoteur`, fiches techniques, préconisations |
| `ADN_RG` | Référentiel : `Utilisateur`, `Employe`, `Societe` → bloc **diagnostiqueur** |

**Pivot métier important :** pour une mission DPE, `XDPEsaisieLot` contient typiquement 4 lignes :
- `-2` = état actuel du logement (**lot de calcul principal**)
- `-3 / -4 / -5` = recommandations / travaux (clés bouquet `ESS`, `PRV`, `PCK`)

Le service ne traite pour l'instant que le **lot principal** (`currentLot`) pour les blocs enveloppe/installations.

---

## 6. La doc de rétro-ingénierie (à lire absolument)

Dans `docs/` — c'est le livrable le plus précieux, c'est le travail de cartographie de la base :

- **`dpe-xml-control-guide.md`** — état du mapping bloc par bloc + sources confirmées.
- **`dpe-xml-database-research.md`** — journal détaillé des requêtes SQL vérifiées, table par table, colonne par colonne, avec hypothèses à valider.
- **`dpe-xml-field-control.csv`** — matrice exhaustive : 1 ligne par chemin XML feuille de l'officiel, avec statut (`Couvre` / `Partiel` / `Manquant` / `A qualifier`) et source pressentie.
- **`operations-guide.md`** — événements journalisés, bonnes pratiques, diagnostic.
- **`README.md`** — vue d'ensemble.

---

## 7. État d'avancement réel ⚠️

D'après `dpe-xml-control-guide.md` (snapshot daté du 2026-06-08) :

- **504** chemins XML feuilles dans le DPE officiel
- **47** présents dans le XML reconstruit ; **11** `Couvre`, **46** `Partiel`, **446** `Manquant`

**Nuance importante constatée en relisant le code :** `DpeXmlDocumentBuilder` contient déjà des
*builders* pour bien plus de blocs que ne le suggèrent ces chiffres (enveloppe, murs, fenêtres,
portes, planchers, ponts thermiques, chauffage, ECS, ventilation, clim, sorties moteur, confort
d'été, qualité d'isolation, recommandations de travaux, fiches techniques, justificatifs…).
→ **La tuyauterie est plus avancée que le CSV ne le dit.** Le CSV date probablement d'avant ces ajouts,
ou compte des feuilles encore non alignées sur les chemins officiels.

**Conclusion : la première mesure à refaire = re-générer le diff entre un XML reconstruit *avec le code actuel* et un XML officiel, pour avoir le vrai taux de couverture aujourd'hui.**

### Ce qui est solide
- Architecture, DI, configuration, logging, masquage des données sensibles.
- Détection exe + détection type de base + connexion LocalDB read-only.
- Suite de tests sur la logique non-UI (`DpeXmlDocumentBuilderTests` fait 22 Ko à lui seul).
- Blocs confirmés : administratif (dates, consentement, commanditaire), géolocalisation (adresse bien + propriétaire), caractéristique générale logement, météo/altitude.

### Ce qui reste / points ouverts (issus de la doc)
- **Bloc diagnostiqueur** : vient de `ADN_RG`, mais sur un dossier *importé* il peut diverger de l'officiel (technicien remappé). Règle d'import ADN à confirmer.
- Confirmer que `referenceAdm` ⇒ nœuds XML `reference`.
- Signification métier de `ESS` / `PRV` / `PCK`.
- Ordre fonctionnel attendu entre lignes `XDPEsaisieLot` et blocs travaux.
- Adresse encore reconstruite trop simplement (privilégier `infoBAN` / interlocuteur propriétaire).
- `hsp` : vérifier si `XDPEHSPValue` doit primer sur `hauteurSsPlafond` selon les dossiers.

---

## 7bis. ✅ Validation sur données réelles Analys'immo (2026-06-23)

Réalisée sur l'install **ADN evaluation** présente sur le poste de Gabriel.

### Environnement réel découvert
- ADN/Analys'immo installé dans **`C:\ADN_Evaluation`** (et non `C:\ADN`) → `ExecutableDiscovery.SearchRoots`
  du `appsettings.json` doit inclure ce chemin, ou activer `SearchFixedDrivesIfNotFound`.
- ⚠️ **L'install d'évaluation tourne en SQL Server Compact (`.sdf`), pas en LocalDB.**
  Bases dans `C:\ADN_Evaluation\Synchro\SDLDEMO\` : `ADN_DIAG.sdf` (44 Mo), `ADN_DIAG_DPE2012.sdf`
  (18 Mo, **487 tables**), `ADN_RG.sdf`, `ADN_BIN.sdf`, `ADN_UPDATE.sdf`. Moteur **SQL CE v3.5** installé.
- Le plugin ne parle qu'à **LocalDB** et refuse tout le reste → **il ne reconstruit pas en l'état sur cette install**.
  Rappel : les vraies installs ADN utilisent une instance **LocalDB de synchro** (le plugin est fait pour ça) ;
  l'évaluation est le cas particulier SQL CE.
- Contrainte clé : **`System.Data.SqlServerCe` n'existe qu'en .NET Framework** → un portage SQL CE du
  plugin .NET 8 n'est pas trivial (process .NET Framework annexe, ou migration `.sdf` → LocalDB).

### Dossier de démo utilisé pour la validation
- *« 2026 - DURAND 17.10.25 »* — `idDossier=-1`, `idMission=-4`, `idEmploye=13`,
  guid `8b724d56-1d1f-4fb6-add0-cc4cbb405203`, **4 lots** (lot principal `idSaisieLot=-1` + 3 recommandations).
- Autres dossiers DPE2021 démo : Syndicat « La rose des sables » (12 lots), 2 dossiers TMP.

### Résultat : mapping massivement valide sur données réelles
Harnais `tools/Validate-Mapping.ps1` (extrait les ~26 requêtes du service, les adapte SQL CE, les rejoue) :
**24 / 26 requêtes OK**, renvoyant des données cohérentes (murs 4, fenêtres 2 + 34 items techniques,
ponts thermiques 17 + 60 items, générateurs chauffage/ECS + 16/11 items, sorties moteur, etc.).

Les **2 seuls échecs** sont dus à une **CTE (`WITH`) non supportée par SQL CE 3.5** (OK en LocalDB) :
- `LoadEnergyPriceSnapshotsAsync` — CTE + `ROW_NUMBER() OVER(PARTITION BY…)` (prix énergie le plus récent
  par combustible). Pour SQL CE : faire le ranking en C#, ou réécrire en sous-requête `MAX(date)`.
- `LoadWallOpeningSurfaceByWallIdAsync` — CTE encapsulant un `UNION ALL` puis `GROUP BY/SUM`. Réécriture
  triviale en table dérivée : `SELECT … FROM ( … UNION ALL … ) AS x GROUP BY WallId`.

→ **Conclusion** : le mapping base→données du plugin est sain sur de vraies données Analys'immo. Les seuls
points de portage SQL CE sont ces 2 requêtes (window function + CTE). `ClimatisationSnapshots` renvoie 0
ligne pour DURAND (pas de clim sur ce logement — normal).

### Ce qui manque encore pour mesurer la COUVERTURE réelle
Il faut un **XML DPE officiel de référence** : ADN n'en a pas encore généré pour les dossiers de démo
(dossiers `…/standard/` vides). **Action Gabriel** : ouvrir le dossier DURAND dans ADN et lancer l'export
DPE 2021 / transfert ADEME pour produire le `0 (DPE 2021).xml`, puis comparer avec
`tools/Compare-DpeXmlCoverage.ps1`.

---

## 8. Verdict & ce qu'on garde

**On garde tel quel :**
- Toute l'ossature (Domain/Application/Infrastructure/TrayApp), DI, config, logging, tray, tests.
- La logique détection exe / détection base / lecture LocalDB read-only.
- La doc de rétro-ingénierie et le CSV de contrôle.
- Le `DpeXmlDocumentBuilder` et les snapshots existants comme socle.

**On finit / on fiabilise :**
- Compléter le mapping des 446 chemins manquants (par familles, voir ordre ci-dessous).
- Gérer les lots de recommandation (`-3/-4/-5`), pas seulement le lot principal.
- Résoudre les points ouverts du §7.

**Rien d'« Analys'immo-spécifique » à réécrire** puisque ADN = Analys'immo : pas de re-cartographie
de base à refaire, contrairement à ce qu'on craignait au départ.

---

## 9. Plan de reprise proposé

**Phase 0 — Remise en route (½ j)**
1. ✅ **Fait** — SDK .NET 8.0.422 installé, `restore`/`build`/`test` verts (11/11). Voir §2.
2. ⏳ Lancer sur un poste **avec ADN installé + base de test** (`DefaultDossierReference = "137483 PETIT"`).
   (Pas réalisable sur la machine actuelle : pas d'ADN ni d'instance LocalDB ici.)
3. ⏳ Reconstruire un XML et le comparer à l'officiel `0 (DPE 2021).xml` avec
   `tools/Compare-DpeXmlCoverage.ps1` → **mesurer le vrai taux de couverture actuel**.

**Phase 1 — Quick wins (faible risque)**
- Fiabiliser administratif + géolocalisation (`DossierInterlocuteur`, `infoBAN`).
- Régler le bloc diagnostiqueur (`ADN_RG`) + trancher la règle d'import.

**Phase 2 — Enveloppe** (le plus gros volume du XML)
- `XDPEdetailSaisieEnvMur / Fenetre / Plancher / Plafond / Porte`, ponts thermiques, isolants.

**Phase 3 — Installations & sorties**
- Générateurs (chauffage/ECS), émetteurs, ventilation, clim, `XDPEsortieMoteur` (conso/coût/émissions déjà calculés en base).

**Phase 4 — Fiches techniques & travaux**
- `XDPEficheTechnique`, `XDPEdetailPreconisation`, packs de travaux.

**Phase 5 — Validation croisée**
- Comparer plusieurs dossiers (cas différents) ; viser un diff vide vs XML officiel sur les blocs non liés au technicien.

---

## 10. Points de vigilance techniques

- **Lecture seule stricte** : ne jamais écrire dans la base d'ADN (risque de corrompre les dossiers du client).
- **Pas d'élévation** de privilèges sauf besoin externe.
- **Données personnelles** : masquage activé dans les logs (`Optimo:Privacy`) — à conserver.
- **Selecteurs UI Automation** dans `appsettings.json` (`ReferenceField.AutomationId = "ReadOnlyReferenceField"`) sont des **exemples** à valider avec Inspect.exe sur la vraie IHM.
- **Démarrage auto** via `HKCU\...\Run` (clé `OptimoTrayApp`) — à confirmer côté déploiement.
- **Nettoyage** : supprimer `bin/`, `obj/`, `.vs/`, `.tmp-dotnet/`, `scratch/` du dépôt et mettre en place un `.gitignore` (c'est ce qui a alourdi l'archive 7z et survécu à l'extraction vide).
- **Encodage** : la base contient des accents mal encodés (ex. `MikaÃ«l`) → attention au mapping latin-1/UTF-8 lors de la reconstruction.

---

## 11. Repères concrets (dossier de test)

- Dossier ADN : `137483 PETIT` — `idDossier = -1`, `idMission DPE2021 = -1`
- `guidSdl` : `1FF7C2FA-3CEB-4C13-8403-999363849DCC`
- Instance LocalDB observée : `ADN_Local_Synchro_SDL20_...`
- XML officiel de référence : `C:\ADN\Reporting\Diagnostic\Dossier\2026\avril\1ff7c2fa-...\standard\0 (DPE 2021).xml`
