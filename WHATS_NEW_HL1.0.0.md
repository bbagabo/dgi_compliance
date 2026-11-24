# DGI Compliance HL v1.0.0 - Version présentée à l'homologation

24 novembre 2026. Première version de la numérotation HL : **HL v1.0.0 = ancienne 5.5.2**, sans changement
fonctionnel par rapport à la 5.5.2 (`WHATS_NEW_5.5.2.md`).

## Un seul numéro pour le SFE

La DGI homologue un seul SFE : l'application, la passerelle et le poste navigateur portent le même numéro.

| Composant | Ancien numéro | HL v1.0.0 |
|---|---|---|
| Application `dgi_compliance` | 5.5.2 | 1.0.0 |
| Passerelle `dgi_gateway` | 0.3.1 | 1.0.0 |
| Poste navigateur (`dgi_mcf.js`) | web-0.3.0 | web-1.0.0 |

Frappe et pip demandent un numéro sans lettres : le code porte `1.0.0`, les documents, les impressions et
l'attestation portent « HL v1.0.0 ».

## Règle de numérotation

| Version | Contenu | Décision |
|---|---|---|
| HL v1.0.x | Corrections sans changement de fonctionnalité | Proposée, validée par le responsable du produit |
| HL v1.x.0 | Changement important de fonctionnalité | Proposée, validée par le responsable du produit |
| HL v2.0.0 | Changement majeur | Responsable du produit seul |

## Ce qui change dans cette version

- Numéros de version de l'application, de la passerelle et du poste navigateur.
- Pied de la facture normalisée et des rapports X, Z et A : « DGI Compliance HL v1.0.0 ».
- Paramètres DGI, version homologuée : « HL v1.0.0 », « HLv1.0.0 », « v1.0.0 » et « 1.0.0 » sont acceptés.
  Le message d'écart affiche les deux versions au format HL.
- `README.md` : règle de numérotation.

## Déploiement

Comme une version ordinaire : déploiement de `main` sur Frappe Cloud, puis mise à jour du site. Aucun patch de
données. Le numéro baisse (5.5.2 vers 1.0.0) : sans effet pour Frappe, le contrôle de la version homologuée
compare l'égalité.

Avant le déploiement en production : l'article bière relié au groupe N doit passer en groupe B, avec l'accise
sur une ligne L (type TAX). Depuis la 5.5.2, un article BIE en groupe N est refusé à la normalisation.

## Tests

| Suite | Résultat |
|---|---|
| `test_groupe_n_calc`, `test_specs_v2_calc`, `test_homologation_scope` (sans base) | 9/9, 6/6, 10/10 |
| Passerelle `dgi_gateway` | 75/75 |
| Poste navigateur | 18/19, comme en 5.5.1 (le cas restant demande Frappe) |
| Intégration (`test_groupe_n_552`, `test_avoirs_tax_552`) | À exécuter sur le site de test |
