# DGI Compliance v3.5.0 — Notes de version

Révision complète de l'application : mapping Tax Group piloté par priorité, correction d'un
bug bloquant de normalisation manuelle, durcissement du client API et des logs.

## 1. Tax Group Mapping avec colonne « Priority » (demande principale)

La table unique **Tax Group Mapping** (DGI Compliance Settings) reçoit une colonne **Priority**
qui rend l'ordre de sélection explicite et configurable (la priorité la plus **basse** gagne) :

| Priority | Source | Niveau |
|---|---|---|
| **10** | Modèle de taxe d'article (Item Tax Template) | Ligne |
| **20** | Modèle Taxes et Frais de Vente (Sales Taxes and Charges Template) | Facture |
| — | Default Tax Group | Uniquement si AUCUNE taxe n'est spécifiée |

- Les priorités vides sont auto-assignées (10 / 20) à l'enregistrement des Settings et par le
  patch de migration `v3_5.set_tax_group_priorities` (lignes obsolètes « taux » → 90).
- Validation d'intégrité de la table : une seule source par ligne (article OU facture),
  doublons ambigus bloqués (même modèle → groupes différents), doublons identiques signalés.
- Le Default Tax Group ne remplace JAMAIS un mapping manquant : taxe spécifiée mais non mappée
  ⇒ blocage explicite (`TaxGroupMappingError`), inchangé depuis v3.4.
- Nouveau bouton **« Vérifier le mapping taxes »** (Settings) : liste les modèles de taxe
  actifs non mappés avant qu'ils ne bloquent une facture.

## 2. Correction majeure : normalisation manuelle jamais confirmée

**Bug v3.4.x** : avec `Auto-normalize on Submit` décoché, le bouton « Normaliser (DGI) »
créait la facture à la DGI (uid) mais **ne la confirmait jamais** — le statut ne pouvait pas
atteindre « Normalized » et le postage restait bloqué indéfiniment.

**v3.5** : l'étape *confirm* s'exécute toujours pendant la normalisation. `auto_normalize`
décide seulement du déclenchement automatique au moment du submit, jamais de l'omission du confirm.

## 3. Fiabilisation du flux de normalisation

- **Garde anti-doublon** : relecture du statut en base avant tout appel — deux utilisateurs
  ne peuvent plus créer deux factures DGI pour le même document.
- **Nettoyage des UID orphelins** : si une tentative précédente a créé sans confirmer
  (réseau coupé), l'ancien uid est annulé (best-effort, journalisé `cancel-stale`) avant re-création.
- **Cohérence paiements** : avertissement si la somme des paiements POS ≠ total facture (CDF).
- **Annulation** : si le cancel DGI échoue (facture déjà confirmée), message clair invitant à
  émettre un avoir FA/EA référencé sur le Code DEF.
- **Détection de modification élargie** : un brouillon normalisé repasse en « Pending » si
  client, devise, taux, modèle de taxes, date ou référence d'avoir changent (pas seulement les totaux).

## 4. Client API durci (`edef/client.py`)

- Jeton manquant → erreur claire immédiate (plus d'aller-retour HTTP 401).
- **Retries automatiques avec backoff** (2 tentatives, 2 s/4 s) sur pannes réseau et HTTP
  502/503/504 — UNIQUEMENT pour les appels idempotents (GET, PUT confirm/cancel).
  `POST /api/invoice` n'est **jamais** rejoué automatiquement (risque de facture en double à la DGI).
- Timeout scindé (connexion 5 s / lecture paramétrable) — un hôte mort échoue vite.
- `duration_ms` et `attempts` dans chaque résultat (visibles dans les logs).

## 5. Logs & audit (`edef/audit.py`)

- **Purge en masse** : un seul DELETE SQL au lieu d'une suppression ligne par ligne
  (des ordres de grandeur plus rapide sur de gros volumes).
- **Plafond de taille** : payloads tronqués à ~50 Ko pour ne jamais gonfler la base.
- Les messages d'erreur > 140 caractères sont conservés en intégralité dans le log
  (`error-detail`), le champ facture ne gardant que le résumé.

## 6. Remises

- `originalPrice` : repli sur `discount_amount` (converti en CDF) quand la ligne n'a pas de
  prix catalogue (`price_list_rate` vide, taux édité manuellement).

## Migration depuis v3.4.x

```bash
bench --site <site> migrate
```

Le patch assigne les priorités aux lignes de mapping existantes ; aucune autre action requise.
Comportement par défaut inchangé : article (10) → facture (20) → défaut (si aucune taxe).
