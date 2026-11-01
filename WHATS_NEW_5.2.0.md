# DGI Compliance v5.2.0 (HL2) — Notes de version

Base : v5.1.0 (branche `feature/HL2-mcf-gateway`). Cette version applique les décisions du
31/10/2026 sur les hypothèses de l'architecture HL2 et supprime le besoin d'un poste allumé en
permanence par point de vente.

> **Toujours sur la branche de travail.** Pas de fusion dans `main` avant l'essai avec un MCF
> physique (procédure fournie).

## Décisions appliquées

| Hypothèse | Décision | Effet dans la v5.2.0 |
|---|---|---|
| H1 — un point de vente peut avoir un e-MCF et un MCF et passer de l'un à l'autre | Confirmée | Bascule manuelle sans référence DGI obligatoire (réglable) |
| H2 — le MCF sert de secours si l'e-MCF tombe | Souhaitée, MCF principal possible | **e-MCF principal, MCF de secours automatique** ; « MCF principal » reste une option par point de vente |
| H3 — un MCF appartient à un seul point de vente | Confirmée | Inchangé (contrôle du NID à chaque échange) |
| H4 — plusieurs caisses partagent un MCF | Confirmée | La file par MCF sert toutes les caisses du point de vente |
| H5 — ventes à crédit sur MCF | **Elles se déclarent sur MCF comme sur e-MCF** | Paiement à crédit transmis au MCF avec la lettre du MCF mis à jour (paramètre) |
| H6 — groupes Q à Z sur MCF | Groupes mis à jour par la DGI, obtenus par mise à jour du MCF et par l'API | Groupes A à Z acceptés ; les groupes réels sont lus sur le MCF (C1h) et sur l'API |
| H7 — avoir sur une facture de l'autre canal | À confirmer, supposé oui | Un avoir référence le code DEF de la facture d'origine, quel que soit son canal |
| H9 — seuils de synchronisation 1 jour / 7 jours | Tout implémenter | Les deux niveaux d'alerte sont actifs |

## Nouveau : le Poste MCF dans le navigateur

- La caisse où le MCF est branché ouvre la page **Poste MCF** d'ERPNext dans **Chrome ou Edge**.
  Le navigateur parle au MCF par le port USB/série (Web Serial). **Rien à installer, pas de poste
  allumé en permanence** : le poste fonctionne quand la caisse fonctionne.
- La page continue de travailler pendant que la caissière utilise ERPNext dans le même onglet ;
  elle se reconnecte seule au MCF débranché puis rebranché et redémarre seule à l'ouverture.
- Mêmes règles que la passerelle programme : une facture à la fois par MCF, journal local,
  rapprochement par C1h, jamais de double normalisation. Le code est un portage fidèle de la
  passerelle Python, testé contre le même simulateur de MCF.
- La passerelle programme (`dgi_gateway`, type « Service (poste dédié) ») reste disponible pour
  les sites qui ont un poste dédié, et comme outil de diagnostic.

## Nouveau : e-MCF principal, MCF de secours

- Point de vente en e-MCF avec un MCF rattaché et « Secours MCF = Automatique » : une facture que
  l'e-MCF **n'a certainement pas reçue** (réseau coupé, délai dépassé, erreur 5xx/401/403/429
  **avant** toute confirmation) part sur le MCF du même point de vente.
- **Jamais de secours après une confirmation e-MCF restée sans réponse** (la facture pourrait déjà
  être normalisée) : la facture reste « incertaine » comme en v5.0.5.
- Dès la première panne prouvée, les factures suivantes vont directement au MCF (pas d'attente
  pour chaque caissière). Chaque minute, après 5 min (réglable), ERPNext teste l'e-MCF ; quand il
  répond, le secours se ferme et les factures encore en file repartent vers l'e-MCF.
- Tout est tracé : événement `SECOURS_MCF` / `RETOUR_EMCF`, opération marquée « secours » avec sa
  cause, bandeau sur le point de vente, bouton « Revenir à l'e-MCF maintenant ».

## Sécurité

| Mesure | Détail |
|---|---|
| Clé du poste non exportable | ECDSA P-256 générée par WebCrypto avec `extractable=false`, conservée dans le profil du navigateur ; aucun script ne peut la lire ni la copier |
| Deux facteurs pour agir | Signature de la clé approuvée **et** jeton opérateur de 12 h obtenu par un utilisateur connecté ayant le rôle **DGI Caisse MCF**, lié au poste |
| Session de la caissière protégée | Les appels du poste partent **sans cookie** ; un appel portant une session est refusé (évite toute confusion de session) |
| Enrôlement | Code unique de 30 min, puis approbation par **une autre personne** que celle qui a enrôlé, avec **saisie obligatoire de l'empreinte** |
| Pas de rétrogradation | L'algorithme de signature vient de la clé enregistrée, jamais de la requête ; une clé Ed25519 est refusée pour un poste navigateur |
| Requêtes | Pas de paramètres d'URL, corps limité à 256 Ko, horodatage ±300 s (horloge du poste corrigée par l'heure du serveur), nonce à usage unique |
| Résultats | Refus d'un résultat pour une opération non confiée à ce poste (`RESULTAT_SANS_BAIL`) ; compteur total du MCF strictement croissant, sinon litige |
| Contrôle des compteurs | À chaque signal, compteurs et dernière facture du MCF comparés à ERPNext : `COMPTEUR_INCOHERENT` (critique), `FACTURES_HORS_ERPNEXT`, `CODE_DEF_DIFFERENT`, `TAUX_MCF_DIFFERENTS` |
| Données locales | Le journal du poste ne garde que des identifiants et résultats (aucune donnée client) ; purge à 30 jours |
| Un seul onglet | Verrou du navigateur : un seul onglet pilote le MCF |

Risque résiduel principal : un script malveillant exécuté dans ERPNext (faille XSS) sur la caisse
pourrait utiliser le port série déjà autorisé. Mesures : profil de navigateur dédié au poste,
stratégies Chrome/Edge limitant le port série au site ERPNext (`DefaultSerialGuardSetting`,
`SerialAskForUrls`, `SerialAllowUsbDevicesForUrls`), et contrôle des compteurs ci-dessus qui révèle
toute normalisation inconnue d'ERPNext.

## Paramètres et données

- DGI Point of Sale : canal principal par défaut **e-MCF**, « Secours MCF si l'e-MCF est
  indisponible » (Automatique / Manuelle / Aucune), « Secours MCF actif depuis » et sa cause.
- DGI Gateway : type « Poste navigateur » (défaut) ou « Service (poste dédié) », algorithme de clé,
  enrôlé par.
- DGI Compliance Settings : lettre du paiement à crédit (35h), référence DGI obligatoire ou non pour
  une bascule manuelle, délai avant nouvel essai de l'e-MCF, interrogation de secours du poste,
  règle des deux personnes pour l'approbation d'un poste.
- DGI MCF Device : groupes et taux annoncés par le MCF, dernière facture du MCF.
- Nouveau rôle **DGI Caisse MCF** et nouvelle page **Poste MCF** (`/app/dgi-mcf-station`).

## Mise à jour

- Patch `v5_2.hl2_station_fallback` : passerelles existantes → « Service (poste dédié) » ; points de
  vente sans canal principal → e-MCF avec secours automatique ; politique crédit → déclaration avec
  la lettre du MCF ; valeurs par défaut des nouveaux paramètres.
- Frappe Cloud : le déploiement construit les fichiers de la page (`/assets/dgi_compliance/js/dgi_mcf.js`).

## Tests (banc ERPNext 16.53 / Frappe 16.54)

| Lot | Résultat |
|---|---|
| Passerelle programme, tests unitaires (dont groupes Q–Z, lettre crédit, 33h à 19 et 26 groupes) | 48/48 |
| Poste navigateur, tests unitaires (sorties identiques à la passerelle Python, scénarios de panne, signature WebCrypto vérifiée par le serveur) | 15/15 |
| HL2, intégration Frappe (H01–H26 : routage, file, litiges, secours e-MCF→MCF, crédit, groupes, compteurs, enrôlement de poste) | 26/26 |
| Application complète (HL1 + HL2) | 98/99 — seul T25 (rendu PDF du banc) échoue, comme sur la v5.0.5 d'origine |
| **Bout en bout dans Chromium** : caissière → page Poste MCF → MCF branché → enrôlement → approbation par une autre personne → e-MCF injoignable → secours automatique → 2 factures normalisées par le navigateur, opératrice tracée, aucun doublon, session intacte | OK |
| Bout en bout HTTP de la passerelle programme (enrôlement, normalisation, rejeu / corps modifié / clé intruse refusés) | OK |

## Limites connues

- **Pas encore d'essai avec un MCF physique** (procédure fournie pour l'essai du 01/11/2026).
- Lettre du paiement à crédit sur le MCF mis à jour : **à obtenir du fournisseur** (non documentée
  dans le protocole v1.0).
- Écran Point de Vente (POS) : pendant un secours, une vente doit d'abord être enregistrée en
  brouillon (message explicite) ; intégration complète du POS à venir.
- Poste navigateur : Chrome ou Edge sur ordinateur (pas Firefox, pas Safari, pas mobile).
