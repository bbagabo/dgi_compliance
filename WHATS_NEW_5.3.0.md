# DGI Compliance 5.3.0 - Routage DGI, secours dans les deux sens, découverte du MCF

Branche `feature/HL2-mcf-gateway`, 1er novembre 2026. Ne pas fusionner dans `main` avant l'essai avec le MCF physique.
Références : livrable A (analyse, décisions validées le 01/11/2026), livrable B (architecture détaillée), livrable D (plan de test).

## Décisions appliquées

| Point | Décision |
|---|---|
| Canal prioritaire | Réglé dans DGI Compliance Settings (e-MCF par défaut) ; un point de vente peut déroger (DGI Fiscal Manager ou System Manager, motif obligatoire). |
| Secours | Dans les deux sens, automatique seulement si le canal prioritaire n'a certainement pas reçu la facture ; retour automatique après un test sans effet fiscal. |
| Point de vente | DGI Point of Sale reste le pivot ; NIM, NID et MID stockés séparément ; un site peut porter plusieurs points de vente (agence rattachée par couple société + agence). |
| Contradictions | Bloquent la validation ; un DGI Fiscal Manager peut trancher avec un motif tracé. |
| Facturation centralisée | Point de vente du lieu d'émission ; un entrepôt d'un autre point de vente est signalé (ou bloqué selon le paramètre). |

## Ce qui change

1. **Résolution du point de vente** (`fiscal/router.py`) : toutes les sources sont lues (point de vente saisi par une personne, POS Profile, agence, entrepôts en option) ; codes de règle EMISSION, EMISSION_CENTRALISEE, ENTREPOT, ARBITRAGE, DEFAUT_PARAMETRES, UNIQUE_SOCIETE, MODE_GLOBAL ; blocages CONFLIT_SOURCES, CONFLIT_ENTREPOTS, AUCUNE_SOURCE, SOCIETE_DIFFERENTE, PVF_INACTIF.
2. **Choix du canal** : priorité (paramètres ou dérogation), bascule manuelle en vigueur, secours ouvert, contrôle d'équipement (PRIORITAIRE_NON_EQUIPE, AUCUN_CANAL). Le jeton général (mode HL1) ne sert jamais de secours : seul l'e-MCF propre au point de vente peut recevoir ses factures en secours.
3. **Secours MCF -> e-MCF** (nouveau) : poste MCF silencieux au moment du routage, ou facture restée en file plus de 60 s sans prise par le poste (retrait sous verrou). Jamais depuis une opération prise par le poste, incertaine, en vérification ou en litige. Retour au MCF quand le poste signale de nouveau et que l'état du MCF est lu.
4. **Journal unique** : DGI Fiscal Operation enregistre aussi les tentatives e-MCF (états En attente, Incertaine, Normalisée, Refusée, Annulée), avec la règle de routage, les sources lues et une explication ; une seule tentative active par facture, tous canaux confondus.
5. **Décision en clair sur la facture** (`dgi_routing_summary`), prévision dès l'enregistrement du brouillon.
6. **Découverte du MCF** : page Poste MCF « Proposer ce MCF » (C1h, 2Bh, C2h sans effet fiscal) -> fiche DGI MCF Device « Détecté » ; validation explicite par un DGI Fiscal Manager (autre personne, NIF concordant, écarts acceptés avec motif) ; « Tester la connexion » ; MID ou NIF différent -> « À revalider ».
7. **Jeton e-MCF contrôlé** : bouton « Tester le jeton e-MCF » ; un jeton qui renvoie un autre NIM rend l'e-MCF inutilisable pour ce point de vente (NIM_JETON_DIFFERENT).
8. **Droits** : dérogation, politique de secours, rattachement d'un POS Profile ou d'un entrepôt, arbitrage, validation d'un MCF réservés à DGI Fiscal Manager / System Manager (contrôle serveur).
9. **Opération refusée** : un responsable peut la renvoyer sur l'autre canal (le refus prouve la non-fiscalisation).

## À faire AVANT la mise à jour d'un site

- Un site qui a **plusieurs points de vente fiscaux actifs** et des factures **sans POS Profile, sans agence et sans point de vente saisi** verra ces factures **bloquées** (AUCUNE_SOURCE), alors qu'elles partaient jusqu'ici avec le jeton général. Renseignez le « Point de vente DGI par défaut » de DGI Compliance Settings, ou le point de vente des POS Profile.
- Un MCF « Actif » jamais lu repasse « Déclaré » et doit être validé avant de recevoir des factures.

## Migration (`patches/v5_3/routage_dgi.py`)

Aucun appel à l'e-MCF ni à un MCF, aucune facture. Canal prioritaire général repris de l'ancien mode ; canal principal d'un point de vente vidé s'il égale le réglage général, sinon conservé comme dérogation (« Reprise de la v5.2.0 ») ; un canal actif différent devient une bascule en vigueur (ou une dérogation), si bien qu'aucun point de vente ne change de canal ; MID validé = dernier MID lu s'il porte le NID du MCF.

## Tests (environnement de test, simulateurs ; aucune facture réelle)

| Suite | Résultat |
|---|---|
| `test_routage_dgi` (nouveau, R01-R10, P01-P05, S01-S06, D01-D06, M01) | 28/28 |
| `test_hl2_mcf` | 26/26 |
| `test_hl1_emcf` + `test_calc_words` | 72/73 (T25 : génération PDF impossible dans cet environnement, inchangé) |
| Suite complète de l'application | 126/127 |
| Navigateur réel, découverte (`tests/js/e2e_discovery.js`) | OK |
| Navigateur réel, secours e-MCF -> MCF (`tests/js/e2e_station.js`) | OK |
| Passerelle programme en HTTP (`dgi_gateway/tests/e2e_http_client.py`) | OK |
| `dgi_gateway` (pytest) / `dgi_mcf.js` (node) | 48/48 / 15/15 |

Non testé : MCF physique, e-MCF réel de la DGI, agence (champ Branch absent de la facture sur le site de test : testé sur une facture en mémoire).
