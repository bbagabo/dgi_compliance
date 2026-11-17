# DGI Compliance 5.4.3 - Opérateur DGI des factures normalisées par un MCF

17 novembre 2026, à partir de `main` (v5.4.2). Correctif.
Aucun changement de ce qui est transmis à la DGI ni de ce qui est imprimé.

## 1. Correction : facture normalisée par le MCF impossible à valider

| Avant | Maintenant |
|---|---|
| Une facture normalisée par un MCF ne pouvait pas être validée : « Impossible de trouver Opérateur DGI : b.olinga ». | Elle se valide normalement. |
| Le champ « Opérateur DGI » (lien vers un utilisateur) recevait le code d'opérateur du MCF (`b.olinga`, 16 caractères au plus). | Il reçoit l'utilisateur ERPNext qui a demandé la normalisation (`b.olinga@helosystems.com`), à défaut le créateur de la facture. |

- Le MCF reçoit toujours le même code d'opérateur (partie avant le `@`, 16 caractères au plus).
- La facture imprimée affiche toujours ce code, celui que le MCF a enregistré, lu dans la requête conservée sur la
  facture. Le nom de l'opérateur ne change pas.
- Les factures normalisées par l'e-MCF n'étaient pas touchées.
- Pourquoi les tests ne l'avaient pas vu : ils normalisaient sous `Administrator`, un nom sans `@` qui reste un
  utilisateur valide une fois raccourci.

## 2. Réparation des factures déjà touchées

Le patch `v5_4.operateur_mcf` (exécuté par `bench migrate`) remplace, sur les factures du canal MCF, un opérateur
qui n'est pas un utilisateur par celui qui a demandé la normalisation (`DGI Fiscal Operation` › Demandé par), à
défaut le créateur de la facture. Chaque facture réparée est inscrite au journal (DGI Gateway Event,
`OPERATEUR_CORRIGE`, niveau Info). Les factures restent en brouillon : il reste à cliquer **Valider**.

## Mise à jour

`bench migrate` (Frappe Cloud : automatique). Aucun champ nouveau, aucun réglage. Recharger la page Poste MCF
n'est pas nécessaire (le correctif est côté serveur).

## Tests

| Suite | Contenu |
|---|---|
| `test_operateur_543` (nouvelle) | O01 opérateur = utilisateur et validation possible ; O02 code du MCF imprimé ; O03 repli sur le créateur ; O04 patch de réparation, idempotent, journalisé |

À exécuter sur un site de test : `bench --site <site> run-tests --module dgi_compliance.tests.test_operateur_543`.
Contrôle hors banc fait à la livraison : compilation, et les trois fonctions modifiées exercées sur les cas réels
du site (factures 00019 et 00020). La suite Frappe n'a pas été exécutée sur un banc avant publication.
