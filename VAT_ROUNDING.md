# Arrondi de la TVA au supérieur (plafond)

## Pourquoi ce module
ERPNext v16 n'a **aucune** option « toujours arrondir au supérieur ». *System Settings → Rounding
Method* ne propose que **Banker's Rounding** et **Commercial Rounding**, deux arrondis **au plus
proche**. Ce module ajoute un arrondi **plafond** (ceiling), **TVA uniquement**, avec un **nombre de
décimales configurable**, plus une **réconciliation** avec l'e-MCF.

## Ce qui est livré
- Arrondi au plafond appliqué sur la **facture ERPNext** (hook `validate`), donc montants de taxe,
  total taxes, grand total, total arrondi et écritures comptables reflètent la TVA arrondie au-dessus.
- **Réconciliation** au moment du `submit` : comparaison TVA ERPNext ↔ `vtotal` calculé par l'e-MCF
  (l'e-MCF reste l'autorité fiscale), avec alerte ou blocage selon le réglage.
- **Désactivé par défaut.** À activer et **tester en staging** avant la production.

## Configuration — DGI Compliance Settings → section « VAT Rounding »
| Champ | Rôle |
|---|---|
| **Round VAT up (ceiling)** | Active l'arrondi plafond. |
| **VAT Rounding Decimals** | Nombre de décimales du plafond. `0` = au Franc entier (CDF) ; `2` = au centime (USD). Évolutif. |
| **VAT Accounts** | Liste des **comptes de taxe** considérés comme TVA. Seules les lignes *Sales Taxes and Charges* dont l'`Account Head` y figure sont arrondies. |
| **Reconcile VAT with e-MCF** | Compare la TVA ERPNext au `vtotal` de l'e-MCF au submit. |
| **Reconcile Tolerance** | Écart maximal toléré (ex. `1` CDF) avant action. |
| **On VAT Mismatch** | `Alert only` = normalise quand même + journalise ; `Block` = annule la demande et passe la facture en *Error*. |

## Comment l'arrondi est calculé
`round_up(valeur, décimales)` arronit la **magnitude** vers le haut en conservant le signe :
- `round_up(1240.2, 0) = 1241`
- `round_up(12.341, 2) = 12.35`
- `round_up(99.4, 0) = 100`
- Note d'avoir (TVA négative) : `round_up(-12.2, 0) = -13` (magnitude au-dessus).

La comptabilité reste **équilibrée** : le delta d'arrondi est ajouté à la fois à la ligne de taxe
(TVA à payer) et au grand total (créance), donc le grand livre reste balancé.

## Important : l'e-MCF calcule sa propre TVA
Dans le flux e-DEF, vous envoyez prix + groupe de taxe et **l'e-MCF calcule** `total`/`vtotal`. On ne
peut pas forcer l'e-MCF à arrondir au plafond. Conséquence : la TVA arrondie d'ERPNext peut différer
de quelques unités du `vtotal` e-MCF. C'est précisément ce que détecte la **réconciliation** :
- *Alert only* : la facture est normalisée avec les valeurs e-MCF (autorité) et un avertissement est
  journalisé/affiché.
- *Block* : la normalisation est annulée pour que vous corrigiez (prix, groupe de taxe, ou décimales)
  avant de re-soumettre.

## Test recommandé (staging)
1. Activez *Round VAT up*, réglez *Decimals* = 0, ajoutez votre compte TVA dans *VAT Accounts*.
2. Créez une facture dont la TVA tombe sur une décimale (ex. base donnant 1240,20).
3. Vérifiez en brouillon : la TVA passe à 1241, grand total +0,80.
4. Soumettez et contrôlez le grand livre (créance vs TVA à payer équilibrés) + l'écart de
   réconciliation éventuel dans *Error Log* (`dgi_compliance.edef[reconcile]`).
