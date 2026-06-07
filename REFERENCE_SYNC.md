# Synchronisation des référentiels DGI

Récupère et **rafraîchit** depuis le serveur DGI les éléments évolutifs, via les endpoints `GET`,
et les stocke localement (consultables et modifiables dans ERPNext). Le **mapping** existant
(modes de paiement, groupes de taxe) est conservé tel quel — cette synchro l'alimente.

## Ce qui est récupéré et où c'est stocké

| Source e-DEF | DocType local | Contenu |
|---|---|---|
| `GET /api/info/status` → `emcfList` | **DGI EMCF** | Points de vente : `nim`, statut, nom, adresses 1‑3, contacts 1‑3 |
| `GET /api/info/clientTypes` | **DGI Reference Value** (cat. *Client Type*) | code + description |
| `GET /api/info/itemTypes` | **DGI Reference Value** (*Item Type*) | code + description |
| `GET /api/info/invoiceTypes` | **DGI Reference Value** (*Invoice Type*) | code + description |
| `GET /api/info/paymentTypes` | **DGI Reference Value** (*Payment Type*) | code + description |
| `GET /api/info/referenceTypes` | **DGI Reference Value** (*Reference Type*) | code + description |
| `GET /api/info/taxGroups` | **DGI Reference Value** (*Tax Group*) | lettre A‑P + valeur (%) |
| `GET /api/info/currencyRates` | **DGI Reference Value** (*Currency Rate*) | devise + taux + date |

L'upsert est **idempotent** : `DGI EMCF` est clé par `nim`, `DGI Reference Value` par
`{catégorie}::{code}`. Re-synchroniser met à jour sans dupliquer ; `last_synced` horodate chaque ligne.

## Déclencher la synchro

### Manuellement (bouton)
**DGI Compliance Settings** → bouton **DGI → « Synchroniser les référentiels DGI »**.
Un résumé s'affiche (nombre d'éléments par catégorie + erreurs éventuelles).

### Automatiquement (scheduler)
Section **Reference Data Sync** des Settings :
- **Auto-sync reference data** : activer/désactiver.
- **Reference Sync Frequency** : `Daily` / `Weekly` / `Monthly`.
  Pour Weekly/Monthly, le **jour** réutilise *Weekday* / *Day of Month* de la section Token Monitoring.

Le job `dgi_compliance.edef.sync.scheduled_sync` tourne quotidiennement et s'exécute selon la cadence
choisie. Test immédiat : **Scheduled Job Type** → ce job → **Execute Now**.

## Consulter les données
- **DGI EMCF** (liste) : vos points de vente synchronisés (Kinshasa, Kisangani, …).
- **DGI Reference Value** (liste, filtrer par *Categorie*) : types client/article/facture/paiement,
  groupes de taxe (avec %), taux de change.

## Utiliser pour le mapping
Les valeurs exactes des **groupes de taxe** (A‑P + %) et des types apparaissent désormais dans
*DGI Reference Value*. Reportez-les dans **Tax Group Mapping** / **Payment Mode Mapping** pour relier
vos `Item Tax Template` / `Mode of Payment` ERPNext aux codes DGI.

## Notes techniques
- **Schéma d'authentification** : champ *Auth Header Scheme* dans Settings. `Bearer` (défaut) envoie
  `Authorization: Bearer <token>` ; `None` envoie le **jeton brut** (certaines instances e-DEF
  l'acceptent, comme dans vos tests curl). Si vous avez un 401 en `Bearer`, basculez sur `None`.
- **Compression** : le client demande `Accept-Encoding: gzip, deflate` pour éviter brotli (`br`)
  et toute dépendance optionnelle.

## Seed au démarrage + repère groupes de taxe

Dès l'installation/mise à jour, les **catalogues constants** du protocole (types facture, paiement,
client, référence, article) sont pré-chargés (seed `dgi_compliance.edef.seed`, insert-only — il
n'écrase jamais une valeur déjà synchronisée). Les **valeurs de groupes de taxe**, **taux de change**
et **points de vente** restent récupérés en direct (spécifiques à votre compte, évolutifs).

D'après votre compte e-MCF (`GET /api/info/taxGroups`), les groupes sont actuellement :

| Taux | Groupes e-DEF |
|---|---|
| **16 %** (TVA normale) | **B**, **F** |
| **5 %** | **C**, **G** |
| **1 %** | **O**, **P** |
| **0 %** (exonéré) | A, D, E, H, I, J, K, L, M, N |

➡️ Dans **Tax Group Mapping**, reliez votre *Item Tax Template* « TVA 16% » au groupe **B** (ou **F**),
« TVA 5% » au groupe **C** (ou **G**), etc. Ces valeurs étant évolutives, relancez la synchro pour les
rafraîchir ; la table *DGI Reference Value* (catégorie *Tax Group*) affiche toujours les % à jour.
