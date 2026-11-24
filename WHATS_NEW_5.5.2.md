# DGI Compliance 5.5.2 - Groupe N (TVA spécifique) et types d'article

Version corrective de la 5.5.1. Passerelle `dgi_gateway` 0.3.1 (simulateur seulement). Poste navigateur inchangé.

## Ce qui change

| Sujet | v5.5.1 | v5.5.2 |
|---|---|---|
| Canal MCF, facture avec une ligne N | Totaux attendus calculés avec N à 0 de TVA. Un MCF conforme (SFE 1.5.8) annonce la TVA du groupe N au sous-total 33h : écart, facture refusée (ECART_TOTAUX) | N compté entièrement en TVA, en mode TTC comme en HT |
| Facture normalisée en devise (USD) | Ligne N imprimée en HT, absente du « Total TVA » | Ligne N imprimée en TVA et ajoutée au « Total TVA » ; équivalent CDF corrigé |
| Ancien format « DGI Sales Invoice » | Même défaut | Même correction (TVA, récapitulatif par groupe, remise) |
| Type d'article et groupe | Aucun contrôle | Tableau DGI « Types d'article » appliqué à la normalisation : TAX seulement en L et N ; BIE et SER jamais en L ou N |
| Option : avoirs avec lignes TAX | Matrice E : FA × TAX et EA × TAX bloqués, donc une facture avec une ligne L ou N ne pouvait pas être annulée ni créditée | Autorisés (patch `avoirs_lignes_tax`, seulement si la règle n'a pas été modifiée) |

Déjà corrects, non modifiés : calcul et contrôle e-MCF, facture en CDF, rapports X, Z et A.

## Exemple : carburant (vente réglementée)

100 litres d'essence : groupe M, 2 390 CDF HT le litre (type BIE) ; groupe N, 250 CDF de TVA spécifique le litre
(type TAX). Montants d'exemple.

| | M | N | Facture |
|---|---|---|---|
| HT | 239 000 | 0 | 239 000 |
| TVA | 0 | 25 000 | 25 000 |
| Total | 239 000 | 25 000 | 264 000 |

## Paramétrage conseillé pour les groupes L et N

- Article de la ligne L ou N : type d'article DGI **TAX**, modèle de taxe d'article sans TVA (taux 0) relié au groupe
  dans le Tax Group Mapping.
- Compte de produit par défaut de l'article : le compte de tiers à reverser (TVA collectée pour N, droit d'accise ou
  taxe pour L), afin que le montant ne soit pas comptabilisé en chiffre d'affaires.
- Ne pas relier un modèle de taxes de facture (Sales Taxes and Charges Template) au groupe L ou N : il placerait
  toutes les lignes de la facture dans ce groupe.

## Tests

| Suite | Résultat |
|---|---|
| `test_groupe_n_calc` (sans base) | 9/9 |
| Calculs v2, périmètre homologué (sans base) | 6/6, 8/8 |
| Passerelle `dgi_gateway` | 75/75 (73 + 2 groupe N) |
| Poste navigateur | 18/19, comme en 5.5.1 (le cas restant demande Frappe) |
| `test_groupe_n_552` (W01–W03), `test_avoirs_tax_552` (W04) | À exécuter sur le site de test |

À faire avant l'homologation : un essai sur le MCF réel avec une ligne N, pour confirmer qu'il annonce la TVA du
groupe N au sous-total 33h (le protocole ne le précise pas ; la règle vient de SFE 1.5.8).
