# DGI Compliance 3.0.0 - Notes de version

Version majeure. Alignement strict avec les regles fiscales DGI RDC, sans modifier les champs
core d'ERPNext. Mise a niveau depuis 2.0.x **idempotente** (patch automatique au `bench migrate`).

## 1. Nettoyage - champs supprimes (client + serveur)

Trois champs hérités sont retirés des formulaires, des DocTypes, des API et des validations :

| Champ supprime | Emplacement | Remplacement |
|---|---|---|
| **DGI Client Type** (`dgi_client_type`) | Sales Invoice | Type de client DGI (`dgi_customer_type`) + mapping (§3) |
| **DGI Payment Type** (`dgi_payment_type`) | Sales Invoice | Mapping Mode de paiement -> e-DEF (DGI Settings) |
| **Groupe TVA (LOC/FOR)** (`custom_dgi_vat_group`) | Sales Invoice | Supprime entierement (§4) |

Le patch `v3_0.cleanup_legacy_fields` supprime ces champs en base (Custom Field + Property Setter
+ colonne) sur les sites existants. **Aucune** ressaisie de ces champs n'est possible.

## 2. ISF - source unique dans DGI Settings

L'ISF est desormais defini **uniquement** dans `DGI Compliance Settings -> ISF (AAA-BBB-NN)`
(champ obligatoire). Il est applique automatiquement a **tous** les types de facture
(FV/EV/FT/ET/FA/EA). Le champ ISF au niveau Company (`dgi_isf_number`) est supprime ; sa valeur
est migree vers les Settings si ces derniers sont vides. Plus aucune divergence possible.

## 3. Mapping Customer Type natif ERPNext -> type DGI (Matrice G)

Nouveau DocType enfant **DGI Customer Type Mapping** (table dans DGI Settings) :

- fait correspondre le `Customer.customer_type` **natif** (Company, Individual, ...) aux types
  DGI (PP/PM/PC/PL/AO) ;
- matrice **modifiable** ; une valeur peut etre marquee « par defaut » (auto-attribution si la
  fiche client ne precise pas de type DGI) ;
- utilisee pour **valider la facture avant normalisation** : un type DGI non autorise pour le
  Customer Type natif bloque la facture (option `Verrouiller le mapping Type de client`).

Valeurs semees par defaut : `Company -> PM` ; `Individual -> PP (defaut) ou PL`.

## 4. Suppression de LOC/FOR partout

La dimension Groupe TVA (LOC/FOR) est retiree du moteur de validation. La **Matrice C** devient
`C - Invoice Type / Country` (Type de facture x Pays). Regles semees par defaut : les types
locaux F* exigent un pays local (CD) ; les types export E* exigent un pays etranger (Non-CD).
Le patch supprime les anciennes lignes Matrice C (a base LOC/FOR) et reseme la nouvelle grille.

## 5. Facture normalisee obligatoire avant postage + file d'attente

- **Garde-fou `before_submit`** : une Sales Invoice ne peut etre **soumise (postee)** que si son
  `Statut DGI = Normalized`. Tant qu'elle ne l'est pas, elle reste **brouillon** : aucun impact
  stock / comptabilite / GL (exactement comme un brouillon, mais avec un statut DGI explicite).
- **Liste dediee** : rapport **« Factures en attente de normalisation »** (brouillons non
  normalises).
- **Normalisation** : automatique au postage si `Auto-normalize on Submit` est coche (defaut) ;
  sinon, bouton **« Normaliser (DGI) »** sur le brouillon. Une facture n'est **complete** qu'apres
  normalisation reussie (code DEF/DGI + QR obtenus).
- **Anti-divergence** : modifier un brouillon deja normalise (montant / type) le repasse
  automatiquement en `Pending` et efface le resultat e-DEF -> re-normalisation requise.
- Reglage `Verrouillage postage (normalisation)` = `Enforce` (defaut) / `Off` (comportement
  classique : normalisation apres postage).

## 6. Types de facture (FV, EV, ET, FT, EA, FA)

Logique integree aux validations, au mapping et a la normalisation :

- **FV / EV** : types standards par defaut (FV local, EV export).
- **FT / ET** : factures d'**acompte / prepaiement** - selection **explicite** du « Type de
  facture DGI » (jamais deduits automatiquement). Matrice E (Acompte x Type d'article).
- **FA / EA** : **avoirs / retours / rabais** - deduits automatiquement d'un retour ERPNext ;
  reference d'origine (Code DEF/DGI) obligatoire. Matrice F (Nature de l'avoir x Type d'article).
- Controles de coherence : un retour doit etre FA/EA ; FT/ET incompatibles avec un retour ;
  `Facture a l'exportation` coherente avec la famille E*.

## Mise a niveau

```bash
bench get-app dgi_compliance <source>   # ou git pull dans apps/dgi_compliance
bench --site <site> migrate             # joue v3_0.cleanup_legacy_fields automatiquement
bench --site <site> clear-cache && bench build --app dgi_compliance
```

Aucune action manuelle sur les champs core d'ERPNext. A verifier apres migration :
ISF dans Settings, table « Customer Type Mapping », Matrice C (Type x Pays).
