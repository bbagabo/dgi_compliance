# Nouveautés v1.4.0

## 1. Devise : montants toujours en CDF (LCY)
Le mapper envoie désormais **tous les montants en CDF** (`base_rate`, `base_net_rate`, `base_amount`),
même pour une facture en USD. `curCode`/`curRate` sont quand même transmis pour que la DGI affiche le
total indicatif en devise. La DGI recalcule tout côté serveur.

## 2. Réconciliation du total + écart de change
Au `submit`, comparaison du **total ERPNext (base CDF)** au **total recalculé par la DGI** :
- **Écart ≤ tolérance** (par défaut **500 CDF**, configurable) : on **retient la valeur DGI** et, si
  *Comptabiliser l'écart de change* est activé, on crée automatiquement une **Journal Entry** qui loge
  la différence dans le **compte d'écart de change configurable** (+ compte de contrepartie).
- **Écart > tolérance** : action *Alert only* (normalise + journalise) ou *Block* (annule la demande,
  facture en **Error**, soumission annulée).

Réglages : *DGI Compliance Settings → Currency Reconciliation (CDF)* — Total Tolerance, comptes
d'écart/contrepartie, action. Le compte de contrepartie doit être un **compte GL sans tiers**.

## 3. Print Format « DGI Sales Invoice »
Format d'impression standard affichant la facture + le **bloc fiscal DGI** : **Code DEF/DGI**, NIM,
compteurs, date, et le **QR code en image** (généré depuis le contenu `qrCode`). Sélectionnez-le dans
*Print → DGI Sales Invoice*. Le QR image est stocké dans un champ caché `custom_dgi_qr_image`
(dépendance `qrcode` ajoutée).

## 4. Bouton « Re-tenter la normalisation DGI »
Sur une **Sales Invoice soumise en statut Error**, un bouton **DGI → Re-tenter la normalisation**
relance tout le flux (create → réconciliation → confirm) sans annuler la facture.

## 5. DocType « DGI Exchange Log » + rétention
Tous les échanges avec la DGI (create/confirm/cancel/reconcile/exchange-diff/retry/token-check) sont
journalisés dans le DocType **DGI Exchange Log** (requête, réponse, statut HTTP, facture liée).
- **Auto-suppression** quotidienne des entrées plus anciennes que **DGI Log Retention (days)**
  (défaut **180 = 6 mois**), réglable dans Settings.
- **Purge manuelle** : bouton *DGI → Purger les logs DGI* (ou liste → suppression groupée).
- Également enregistré auprès du *Log Settings* natif de Frappe.

## À tester en staging (rappel)
La réconciliation peut **annuler une soumission** (mode Block) et l'écart de change **crée des
écritures comptables**. Validez le comportement et le grand livre en staging avant la production.
