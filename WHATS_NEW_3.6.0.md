# DGI Compliance v3.6.0 — Notes de version

## 1. Type de facture DGI jamais vide (défaut FV)
- À la création d'une nouvelle facture de vente (hors avoir), **FV** est présélectionné
  (EV si Export coché) — côté formulaire (JS `onload`) ET côté serveur (`validate`).
- Un retour / avoir reste forcé à FA/EA (inchangé). Décocher « Est un avoir » rebascule
  sur FV/EV au lieu de laisser le champ vide.
- FT/ET restent un choix explicite (acompte) et sont conservés s'ils sont sélectionnés.

## 2. Type de facture DGI sur le Sales Order
- Nouveau champ **Type de facture DGI** (Custom Field, fixtures) sur la commande client,
  limité à FV/FT/EV/ET, FV par défaut, modifiable après soumission (informatif).
- À la facturation d'une commande, la facture **reprend automatiquement** le type du
  Sales Order lié si son propre champ est vide.

## 3. Default Payment Type aligné sur le principe du Default Tax Group
- Un mapping existant (Payment Mode Mapping) gagne TOUJOURS.
- Le **Default Payment Type** ne s'applique QUE si aucun mode de paiement n'est spécifié.
- Un mode de paiement spécifié mais **non mappé bloque la normalisation** avec un message
  explicite (`PaymentTypeMappingError`) au lieu d'envoyer silencieusement le défaut.

## Rappel (hors app) — facture en USD
L'erreur « Party Account … currency (CDF) and document currency (USD) should be same »
est un paramétrage ERPNext natif : créez un compte client (receivable) en devise USD et
affectez-le au client (Customer → Accounting) ou au champ « Debit To » de la facture.
Voir GUIDE_DEPLOIEMENT_v3.6.0.md.
