# DGI Compliance v5.0.5 — Notes de version

Base : v5.0.4. Suppression de la matrice de validation « type de facture / pays ».

## Changement

- La **Matrice C** (« C - Invoice Type / Pays (adresse client) », anciennement « C - Invoice Type /
  Country ») est supprimée :
  - plus de contrôle du type de facture (FV, FT, FA ou EV, ET, EA) en fonction du pays de
    l'adresse du client ;
  - l'option « C » disparaît du DocType *DGI Validation Matrix*, ainsi que la colonne « Pays de
    l'adresse client » ;
  - le paramètre « Code pays local (ISO-2) » de *DGI Compliance Settings*, qui ne servait qu'à
    cette matrice, est retiré.
- La mise à jour supprime toutes les lignes de la Matrice C, quel que soit leur nom selon les
  versions (patch `v5_0.v505_remove_matrix_c`). Les matrices D, E et F sont conservées sans
  changement.
- Une facture à l'exportation est désormais choisie uniquement par la case « Facture à
  l'exportation » (ou le champ « Type de facture DGI »), sans lien avec le pays du client.
- Inchangé : les règles de cohérence du type de facture (avoir FA/EA sur un retour, acompte
  FT/ET, etc.) et la conservation du pays saisi dans la fenêtre de création rapide d'un client
  (v5.0.4).

## Tests

- 61 tests d'intégration et 12 tests unitaires : tous réussis. Les tests propres à la Matrice C
  sont remplacés par des tests qui vérifient sa suppression ; une facture FV pour un client
  étranger et une facture à l'exportation pour un client en RDC sont normalisées.
- Installation neuve et mise à jour depuis une sauvegarde v4 rejouées.
