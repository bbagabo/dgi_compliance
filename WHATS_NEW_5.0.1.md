# DGI Compliance v5.0.1 — Notes de version

Base : v5.0.0 (HL1). Correctif sur le taux de change, suite à la position de la DGI :
le taux de change est une information transmise par le système de facturation ; il est
renseigné sur la facture lors de son émission ; l'obligation de rester à ±1 % du taux de
référence est abandonnée.

## Taux de change

- **Le taux transmis à l'e-MCF est toujours le taux saisi sur la facture** (*Exchange Rate* /
  `conversion_rate` de la Sales Invoice) :
  - `curRate` de la facture ;
  - `currencyRate` de chaque paiement ;
  - prix unitaires en CDF = montants de la facture convertis avec ce même taux.
- **Suppression du contrôle ±1 %** par rapport au taux publié par l'e-MCF (v5.0.0). Les champs
  « Contrôle du taux USD » et « Écart maximal » disparaissent des paramètres.
- Les colonnes « Source du taux » et « Taux manuel » de la matrice des devises sont masquées et
  ignorées. Elles pouvaient remplacer le taux de la facture par un autre taux. La matrice continue
  d'autoriser ou de refuser une devise par type de facture.
- Le taux publié par l'e-MCF (`/api/info/currencyRates`) reste synchronisé à titre informatif
  seulement.
- La facture imprimée affiche le taux exact transmis, dans « Devise » et sous l'équivalent CDF :
  « Taux de change de la facture : 1 USD = 2 830.75 CDF » (jusqu'à 6 décimales si le taux en comporte).

## Paiements = total fiscal (SFE 1.1.19)

Avec un taux de change, les montants en CDF tombent rarement sur des chiffres ronds. ERPNext
arrondit la TVA au centime le plus proche, alors que l'e-MCF l'arrondit au centime supérieur
(SFE 1.5).

Exemple : 2 × 300 USD (B) + 150 USD (C) au taux 2 830,75 :
- total ERPNext : 2 416 045,12 CDF ;
- total fiscal : 2 416 045,13 CDF.

La somme des paiements transmis est désormais égale au total fiscal. L'écart d'arrondi (≤ 1 CDF)
est porté par la ligne CREDIT, sinon par la ligne la plus importante. Les paiements imprimés sur une
facture en CDF et les rapports X/Z suivent les mêmes montants. Un écart supérieur n'est pas
corrigé : c'est une vraie différence, traitée par le contrôle des montants ci-dessous.

## Contrôle des montants (inchangé, confirmé)

Avant la confirmation, le SFE recalcule les montants (règles SFE 1.5) et les compare aux montants
calculés par l'e-MCF, total et groupe par groupe. Si l'écart dépasse le seuil de tolérance
(paramètre « Tolérance », 1 CDF par défaut) et que l'action est *Bloquer* :
1. la demande est annulée (`CANCEL`) ;
2. la facture n'est pas confirmée ;
3. la facture reste en brouillon avec le statut *Error*, sans Code DEF.

Ce comportement est désormais testé aussi pour une facture en USD.

## Divers

- Version de l'application imprimée en pied de facture et de rapport (`DGI Compliance v5.0.1`).
- Tests réussis :
  - 51 tests d'intégration (+2) ;
  - 12 tests unitaires (+3) ;
  - 12 tests de l'agent MCF ;
  - installation neuve et mise à jour depuis une sauvegarde v4, rejouées.
- Aucune migration de données : un simple *Update* du site suffit. Les anciennes valeurs des
  champs supprimés restent en base sans effet.
