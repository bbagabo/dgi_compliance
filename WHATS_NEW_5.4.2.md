# DGI Compliance 5.4.2 - Rapports par point de vente, annulation et rejet d'un Poste MCF

Branche `feature/v5.4.2-rapports-par-pdv-et-rejet-poste`, 12 novembre 2026, à partir de `main` (v5.4.1).
Aucun changement de ce qui est transmis à la DGI. Aucune migration de données.

## 1. Rapports X, Z et A par point de vente fiscal

| Avant | Maintenant |
|---|---|
| Un seul bloc pour toute la société. | Un bloc **par point de vente fiscal** (Sections 1 à 5, ou détail par article pour le rapport A), puis en bas la **synthèse de tous les points de vente réunis**. |
| — | **Un seul point de vente sur la période** : ce point de vente seulement, sans synthèse. Son nom, son code magasin, son NIM e-MCF et son MCF (NID) figurent dans l'en-tête. |
| Toutes les lignes imprimées, même à zéro (rapport X ou Z de 4 pages). | Les **lignes à zéro sont masquées** : types de facture, groupes de taxation, types de paiement, réductions et natures d'avoir sans aucun montant. Une section vide indique « Aucune … sur la période ». |

- Le point de vente d'une facture est celui enregistré sur la facture (`Point de vente DGI`). Les factures sans point
  de vente sont regroupées sous « Sans point de vente ».
- Les factures incomplètes sont comptées dans leur point de vente.
- Rapport A : la quantité en stock d'un point de vente est celle des entrepôts rattachés à ce point de vente
  (avec leurs sous-entrepôts) ; sans entrepôt rattaché, celle de tous les entrepôts de la société.
- La numérotation et la clôture ne changent pas : un rapport Z clôture la période de la société, tous points de
  vente confondus. Les totaux de l'entête du document (nombre de factures, total net TTC, TVA nette) restent
  ceux de la société.
- Les données enregistrées dans le rapport restent complètes (tous les types, tous les groupes) ; le masquage
  ne concerne que l'impression.
- Les rapports générés avant la 5.4.2 s'impriment en un seul bloc (ils ne contiennent pas le détail par point de
  vente), avec les lignes à zéro masquées.

**Réglage** : DGI Compliance Settings > *Rapports X / Z / A* > « Masquer les lignes à zéro dans les rapports X et Z »
(coché à l'installation et à la mise à jour). Décoché : toutes les lignes sont imprimées, comme sur le rapport
e-UF de la DGI.

## 2. Poste MCF : annuler une demande, rejeter une approbation

Deux actions nouvelles, réservées au **DGI Fiscal Manager** et au **DGI Approbateur** (jamais un compte technique),
avec un **motif obligatoire** :

| Action | État du poste | Effet |
|---|---|---|
| **Annuler la demande** | « A approuver » (« En attente d'approbation. Empreinte de la clé de ce navigateur : … ») | La clé reçue est effacée, le poste revient à « Nouvelle ». Journal : `DEMANDE_ANNULEE`. |
| **Rejeter l'approbation** | « Active » (poste approuvé) | Le poste s'arrête immédiatement (ses requêtes signées sont refusées), son compte technique est désactivé, sa clé est effacée, le poste revient à « Nouvelle ». Journal : `APPROBATION_REJETEE`. |

- Où : page **Poste MCF** (bouton « Annuler la demande » sous l'empreinte en attente ; bouton « Rejeter
  l'approbation » à côté de « Arrêter le poste ») et fiche **DGI Gateway** (menu *Passerelle*). Les boutons ne
  sont affichés qu'aux deux rôles ; le serveur contrôle le rôle et le droit de lecture du point de vente.
- Pour remettre le poste en service : nouveau code d'enrôlement, enrôlement, puis approbation par une autre
  personne (règle des deux personnes inchangée).
- La règle des deux personnes ne s'applique pas à ces deux actions : retirer un droit est toujours permis à un
  approbateur, y compris à la personne qui a enrôlé le poste.
- Si un responsable annule ou rejette depuis un autre ordinateur, la page Poste MCF de la caisse l'indique
  seule (en moins de 10 s en attente d'approbation, au cycle suivant pour un poste en marche) et repropose
  l'enrôlement.
- Différence avec « Révoquer » (DGI Fiscal Manager seulement) : un poste révoqué est fermé définitivement ; un
  poste dont la demande est annulée ou l'approbation rejetée peut être enrôlé de nouveau.
- Chaque action est journalisée au niveau Alerte (auteur, rôle, motif, empreinte, personne qui avait enrôlé et
  approuvé) et notifiée aux DGI Fiscal Managers.

## 3. Correction

Une alerte envoyée par e-mail pouvait afficher à l'utilisateur un message technique du serveur
(« Please setup default outgoing Email Account ») quand aucun compte d'envoi n'est configuré. Ce message n'est
plus affiché ; l'alerte reste dans le journal et dans les notifications.

## Mise à jour

`bench migrate` puis `bench build --app dgi_compliance` (Frappe Cloud : automatique). Un champ nouveau dans
DGI Compliance Settings, un patch (`v5_4.rapports_par_pdv`) qui coche le masquage des lignes à zéro.
Après la mise à jour, recharger la page Poste MCF sur chaque caisse (Ctrl + F5).

## Tests (bench Frappe 16.56 / ERPNext 16, simulateurs ; aucune facture réelle)

| Suite | Résultat |
|---|---|
| `test_rapports_pdv` (nouvelle) : P01–P07 rapports, G01–G05 Poste MCF | 12 / 12 |
| `test_hl2_mcf`, `test_deux_utilisateurs`, `test_revue_541`, `test_routage_dgi`, `test_sequence_pdv`, `test_statut_mcf`, `test_calc_words` | 123 / 123 |
| `test_hl1_emcf` | 60 / 61 — `T25_print_cdf` (PDF de la facture) non exécutable sur le banc d'essai : le téléchargement de Chromium y est bloqué. Sans rapport avec cette version. |
| Navigateur réel (Playwright) `e2e_retrait_542.js` (nouveau) : D1–D3, E1–E4, F1–F7, G1–G4 | 18 / 18 |
| Navigateur réel `e2e_revue_541.js` (non-régression) | OK |
| Migration : patch exécuté par `bench migrate`, réglage coché | OK |

Non testé : génération PDF par Frappe (wkhtmltopdf / Chromium) du nouveau format ; la mise en page a été
contrôlée en rendant le HTML du format d'impression dans Chromium.
