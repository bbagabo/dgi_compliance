# DGI Compliance 5.4.1 - Revue de la passerelle et corrections

Branche `feature/HL2-v5.4.1-revue-passerelle`, 11 novembre 2026, à partir de `main` (v5.4.0).

Revue complète de la v5.4.0 avant le dossier d'homologation : passerelle (enrôlement, API signée, Poste MCF),
séparation des tâches, champs fiscaux, référence séquentielle, migration. Chaque anomalie a été reproduite
par un test avant d'être corrigée. Aucune migration de données, aucun changement de ce qui est transmis à la DGI.

## Anomalies corrigées

### Intégrité fiscale (exigence SFE 1.5)

| # | Anomalie | Correction | Test |
|---|---|---|---|
| A1 | **Éléments de sécurité falsifiables sur un brouillon.** Ils n'étaient en lecture seule que sur le formulaire : par l'API, un utilisateur autorisé à modifier un brouillon pouvait écrire un statut « Normalized » et un Code DEF/DGI de son choix, puis valider la facture sans normalisation. | Les champs écrits par le SFE (éléments de sécurité, trace fiscale, compteurs d'impression, référence) ne sont jamais pris d'un formulaire ou d'un appel API : la valeur enregistrée est conservée, une tentative est journalisée `CHAMPS_FISCAUX_IGNORES`. | V01–V03 |
| A2 | Après validation, le compteur d'impression pouvait être remis à 0 par l'API (une nouvelle impression sortait « ORIGINAL »). | Compteurs d'impression, référence et message d'erreur conservés tels qu'enregistrés. | V04 |
| A3 | **Modifier une facture normalisée validée depuis le formulaire était refusé** (« Champs fiscaux DGI non modifiables : custom_dgi_normalized_on ») : n° de commande client, en-tête, projet, centre de coût. Le formulaire renvoie les dates en texte, comparées telles quelles. Présent depuis la v5.0. | Comparaison par valeur (`fiscal/compare.py`). | V18 |

### Passerelle et Poste MCF

| # | Anomalie | Correction | Test |
|---|---|---|---|
| A4 | Champs système de la passerelle modifiables par l'API : un DGI Fiscal Manager pouvait créer une passerelle directement « Active » avec sa propre clé, ou changer « Enrôlée par » pour approuver seul. | Une passerelle naît « Nouvelle », sans clé ; statut, clé, empreinte, compte technique, enrôlement et approbation ne changent que par les actions. Tentative journalisée `MODIFICATION_REFUSEE` (Critique). Notes, port et vitesse restent modifiables. | V05, V16 |
| A5 | Passerelle « Service » : la règle des deux personnes ne s'appliquait pas (personne n'est connecté à l'enrôlement). Le responsable pouvait générer le code, enrôler une clé et approuver seul. | L'émetteur du code est l'initiateur : une autre personne approuve. | V15 |
| A6 | **Code d'enrôlement refusé = caissière déconnectée d'ERPNext** (« La session a expiré »), et la page n'affichait rien. | Message clair sur la page, la session reste ouverte. Le point d'accès public de la passerelle « Service » répond toujours 401. | V09, navigateur A |
| A7 | **Jeton opérateur expiré ou perdu** (PC en veille plus de 12 h, redémarrage du serveur) : le poste réessayait sans fin, les factures attendaient jusqu'au rechargement de la page. | Le poste renouvelle sa session avec la session de la caissière et repart seul ; sinon il s'arrête avec la marche à suivre. | navigateur B |
| A8 | **Alertes en rafale.** Chaque requête refusée (signature, horloge, opérateur) écrivait une alerte et envoyait un e-mail aux responsables : un poste au jeton expiré en produisait une toutes les 30 s ; quiconque connaissait l'identifiant d'un poste pouvait inonder le journal sans être authentifié. | Une ligne et une alerte au plus par poste et par type de refus toutes les 10 minutes ; les refus intermédiaires sont comptés sur la ligne suivante. | V17 |
| A9 | Nouveau PC ou nouveau navigateur sur un poste actif : la page demandait de révoquer le poste, or un poste révoqué ne peut plus être enrôlé. | La page propose l'enrôlement sur place avec un nouveau code ; l'ancien navigateur s'arrête ; nouvelle approbation par une autre personne. | navigateur C |
| A10 | Refus non journalisés : la requête refusée étant annulée, la ligne du journal l'était aussi (empreinte erronée, auto-approbation, résultat pour une opération d'un autre MCF ou jamais confiée). | La ligne est enregistrée avant le refus. | V10, V11 |
| A11 | **Annulation et prise en charge simultanées d'une opération** : sans verrou, une opération pouvait être annulée dans ERPNext juste après avoir été remise au poste, qui la faisait normaliser par le MCF. | Lecture du statut sous verrou de ligne des deux côtés : une opération remise au poste ne s'annule plus, une opération annulée n'est jamais remise. | V12, V13 |
| A16 | Une requête de passerelle refusée pouvait renvoyer à un appelant non authentifié un message interne du serveur (configuration de la messagerie). | Seul le refus est renvoyé. | HTTP réel |

### Séparation des tâches

| # | Anomalie | Correction | Test |
|---|---|---|---|
| A12 | Lectures et validation d'un MCF modifiables par l'API (« Proposé par », NIF, MID). | Champs écrits par le SFE seulement ; un MCF déclaré à la main naît sans lecture. | V06 |
| A13 | Le « proposant » d'un MCF restait le premier lecteur : une autre personne pouvait envoyer une nouvelle lecture puis la valider elle-même. Un test de connexion pouvait remplacer le MID en attente de validation. | Le proposant est la dernière personne qui a fourni la lecture à valider ; un test ne modifie pas un MCF en attente. | V07 |
| A14 | Le motif « Mise en service » dispensait toute bascule de la règle des deux personnes. | Dispense limitée à une vraie mise en service : première bascule d'un point de vente qui n'a encore normalisé aucune facture. Sinon, approbation par une seconde personne comme toute bascule. | V08 |
| A15 | Un approbateur pouvait approuver pour un point de vente qu'il n'a pas le droit de lire. | Contrôle du droit de lecture du point de vente. | — |

## Vérifié, sans anomalie

Référence séquentielle (série continue, concurrence, reprise), migration 5.3.2 → 5.4.1 sur une sauvegarde réelle,
installation neuve, facture créée et validée en un seul appel (V14), formats d'impression, passerelle « Service »
de bout en bout par HTTP.

## Mise à jour

`bench migrate` puis `bench build --app dgi_compliance` (Frappe Cloud : automatique). Aucun patch, aucun champ nouveau.
Après la mise à jour, recharger la page Poste MCF sur chaque caisse (Ctrl + F5) : le script du poste passe en `web-0.2.2`.

Changements visibles :
- une passerelle « Service » doit être approuvée par une autre personne que celle qui a généré son code ;
- « Mise en service » ne dispense plus de la seconde personne sur un point de vente qui facture déjà.

## Tests (bench Frappe 16.55 / ERPNext 16.54, simulateurs ; aucune facture réelle)

| Suite | Résultat |
|---|---|
| `test_revue_541` (nouveau, V01 à V18) | 18/18 |
| Suite complète de l'application (166 tests v5.4.0 + 18) | 184/184 |
| Suite complète sur un site neuf (`install-app`) | 184/184 |
| Migration d'une sauvegarde réelle v5.3.2 vers 5.4.1 (`migrate`), puis suite complète | OK : rôle créé, 10 codes de séquence, 42 factures historiques non renumérotées, 3 postes actifs inchangés, 2 formats d'impression à jour ; 184/184 |
| Navigateur réel, `e2e_revue_541.js` (nouveau) : code refusé sans déconnexion, jeton perdu puis reprise, nouveau navigateur sur un poste actif | OK (16 contrôles) |
| Navigateur réel, `e2e_station.js`, `e2e_statut_mcf.js`, `e2e_discovery.js`, `e2e_desk_search.js` | OK |
| Passerelle « Service » par HTTP réel (`e2e_http_client.py` : enrôlement, approbation, facture, rejeu et altération refusés) | OK |
| `dgi_mcf.js` (node) / `dgi_gateway` (pytest) | 15/15 / 48/48 |

Non testé : MCF physique avec cette version et e-MCF réel de la DGI (simulateurs uniquement) ; script
d'installation Windows de la passerelle « Service ».
