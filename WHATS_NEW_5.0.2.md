# DGI Compliance v5.0.2 — Notes de version

Base : v5.0.1. Correctif sur l'impression des rapports fiscaux X, Z et A.

## Problème corrigé

Le DocType *DGI Rapport Fiscal* n'avait pas de format d'impression par défaut. Le bouton *Imprimer*
utilisait donc le format « Standard » de Frappe, qui affichait le champ technique « Données du
rapport (JSON) ».

## Correctifs

- **Format par défaut** du DocType : « DGI Rapport Fiscal ». Le champ JSON est masqué dans le
  formulaire et n'est plus jamais imprimé, même avec le format Standard.
- **Mise en page du rapport e-UF de la DGI** (modèle fourni : X quotidien, Z #3, A #4) :
  - titres « X rapport quotidien », « X rapport périodique », « Z rapport #N », « A rapport #N » ;
  - tableau d'identification : Nom, NIF, ISF, Date du rapport, Début de période, Fin de période ;
  - Section 1 : totaux par type de facture (nombre, factures incomplètes, montant total, HT, TVA,
    taxe spécifique) ;
  - Section 2 : totaux par groupe de taxation par type de facture. Les groupes A à S sont listés pour
    chaque type, même à zéro, comme sur le modèle ;
  - Section 3 : totaux par type de paiement (montant net : ventes moins remboursements) ;
  - Section 4 : réductions et réductions d'impôt par type de facture ;
  - Section 5 : autres enregistrements ayant réduit les ventes (factures d'avoir par nature), exigés
    par SFE 1.3.3 l) ;
  - montants au format du modèle (`59.202.298.067,07`, sans symbole monétaire) ;
  - rapport A : code, désignation, prix, groupe de taxation, taux d'impôt, quantité vendue, quantité
    retournée, quantité en stock (SFE 1.4.2 f), plus le montant collecté (SFE 1.4.1).
- **En-tête de la société** : le papier à en-tête (Letter Head) du site est repris en haut du
  rapport. Il est répété sur chaque page si l'option *Répéter l'en-tête et le pied de page* des
  paramètres d'impression est cochée, comme avec le format Standard.

## Exigences couvertes (Note de service n°01/022, spécifications SFE)

| Exigence | Où |
|---|---|
| 1.3.3 a) dénomination, b) NIF, f) ISF | Tableau d'identification |
| 1.3.3 c) date et heure, e) période | Date du rapport, début et fin de période |
| 1.3.3 d) type de rapport (Z, X quotidien, X périodique) | Titre |
| 1.3.3 g), i), m) totaux, nombre de factures et ventes incomplètes par type | Section 1 |
| 1.3.3 h) totaux par groupe et par type | Section 2 |
| 1.3.3 j) montants par mode de paiement | Section 3 |
| 1.3.3 k) réductions commerciales | Section 4 |
| 1.3.3 l) autres enregistrements ayant réduit les ventes | Section 5 |
| 1.4.2 a)–e) identification du rapport A | Titre et tableau d'identification |
| 1.4.2 f) détail par article | Tableau des articles |

## Tests

- 53 tests d'intégration (+2), 12 tests unitaires, 12 tests de l'agent MCF : tous réussis.
- Installation neuve et mise à jour depuis une sauvegarde v4 rejouées.
- Aucune migration de données : un simple *Update* du site suffit. Les rapports déjà générés
  s'impriment avec le nouveau format, car leurs données sont conservées.
