# DGI Compliance 5.5.0 - Spécifications techniques v2 de la DGI (octobre 2026)

18 novembre 2026, sur `main` (à partir de v5.4.3). Passerelle `dgi_gateway` 0.3.0, poste navigateur `web-0.3.0`.

Documents de la DGI pris en compte :

| Document | Objet |
|---|---|
| Note de service n°01/209/DGI/DG/DLCFI/FMM/2026 du 14 octobre 2026 | Nouvelles spécifications fonctionnelles et techniques des SFE (19 groupes, SFE 1.1.12) |
| Note de service n°01/220/DGI/DG/DLCFI/FMM/LLM/2026 du 26 octobre 2026 | Procédure d'homologation et de ré-homologation |
| FACNO Groupes de taxation v2.0 (octobre 2026) | A à S applicables, T à Z réservés, hors champ en S |
| Annexe Protocoles MCF-SFE v2.0 | C1h et 33h avec `ST1` / `ST2`, 31h de A à Z |
| Protocole e-MCF-SFE v2 (API 2.0) | Taux `trq`…`trz`, montants `taa`…`taz`, `haa`…`haz`, `vaa`…`vaz` |
| Guide de migration vers la version 2 | Synthèse des changements |
| Tableau de conformité des spécifications techniques de SFE v2 | 1.12 : 19 groupes ; plus de mention « applicable uniquement au e-MCF » |

## 1. Ce qui change

| Sujet | v5.4.3 | v5.5.0 |
|---|---|---|
| e-MCF API 2.0, taux des groupes Q à Z | Lus dans `tq`, `tr`… (absents en v2) : taux 0, écart, **facture annulée** dès qu'un article est en Q ou R | Lus dans `trq`…`trz` ; à défaut, taux du catalogue DGI Tax Group |
| e-MCF API 2.0, champ `ts` | Lu comme le taux du groupe S : une facture avec un article S et une taxe spécifique était **annulée** | `ts` = montant de la taxe spécifique, jamais un taux |
| MCF au protocole v2, groupes Q à Z | C1h ne renvoie plus que A à P : Q, R et S **refusés** (« groupe inconnu ») | C1h puis C1h `ST2` : les 26 groupes sont connus |
| MCF au protocole v2, sous-total 33h | TVA de Q et R absente du total : écart ou 38h refusée | 33h puis 33h `ST2` ; TVA totale = somme des 26 groupes |
| Opération hors champ | Groupe A + code article `HORS CHAMP` (communiqué n°010) | **Groupe S** avec le vrai code article ; l'ancienne règle reste une option |
| Article hors champ avec un modèle de taxe à TVA | Accepté | Refusé avec un message clair (pas de changement de groupe silencieux) |
| Groupes T à Z | Acceptés dans le mapping et sur la facture | Réservés : refusés partout (mapping, groupe par défaut, facture, MCF, passerelle) ; catalogue « Réservé 1 à 7 », inactif |
| Diagnostic du MCF | « protocole v1.0 » si 16 groupes | Protocole v1 ou v2 affiché ; contrôle PROTOCOLE dans « Tester le MCF » |
| Tester la connexion e-MCF | Version affichée | Alerte si l'e-MCF n'annonce pas l'API 2.x |

Ce qui ne change pas : principe de normalisation, flux SFE ↔ MCF / e-MCF, contrôle des montants avant
confirmation, éléments de sécurité, QR code, factures et rapports des groupes A à P.

## 2. e-MCF (API 2.0)

- `edef/calc.py` : `rate_field()` donne le nom du taux d'un groupe (`ta`…`tp`, `trq`…`trz`). `response_rates()` et
  `response_groups()` l'utilisent. Les noms de la v1.x (`tq`, `tr`, `tt`…`tz`) restent acceptés en repli,
  jamais `ts`.
- `edef/tasks.py` (`verify_amounts`) : un groupe de la facture sans taux dans la réponse prend le taux du catalogue
  local, au lieu de 0.
- `edef/sync.py` : `/api/info/taxGroups` renvoie `a`…`z`. Les groupes T à Z sont créés réservés et inactifs.
  « Tester la connexion » signale une API autre que 2.x.

Exemple vérifié (B avec taxe spécifique 460, Q 8 %, S hors champ, mode HT) : la v5.4.3 calculait
« Total TVA : SFE=3173,60 / e-MCF=953,60 » et annulait la demande. La v5.5.0 trouve 953,60 des deux côtés.

## 3. MCF physique (protocole MCF-SFE v2)

| MCF branché | Lecture (C1h) | Sous-total (33h) | Groupes Q, R, S |
|---|---|---|---|
| Protocole v2 | C1h, puis C1h `ST2` | 33h, puis 33h `ST2` | Acceptés |
| Protocole v1 (non mis à jour) | C1h ; `ST2` refusée ou ignorée, retenue pour ce MCF | 33h | Refusés avant toute commande : « le groupe Q demande la mise à jour du MCF au protocole v2 » |
| Micrologiciel intermédiaire (groupes en ligne dans C1h) | C1h seule | 33h seule | Acceptés comme en 5.4.x |

- Passerelle Python (`dgi_gateway` 0.3.0) : `protocol.py` (`parse_status_st2`, `merge_status_st2`,
  `parse_subtotal_st2`, `merge_subtotal`), `device.py`, `fiscalize.py` (`GROUPE_RESERVE`), simulateur v2.
- Poste navigateur (`public/js/dgi_mcf.js`, `web-0.3.0`) : même logique, mêmes messages.
- Côté ERPNext (`fiscal/mcf_payload.py`) : groupe réservé refusé ; groupe absent du MCF refusé avec le message
  du protocole v2.
- Une facture A à P sur un MCF v1 se normalise comme avant. Une passerelle 0.2.0 non mise à jour devant un MCF v2
  refuse Q, R et S (refus net, jamais de normalisation fausse).

## 4. Groupes de taxation

- Constantes uniques dans `edef/calc.py` : `APPLICABLE_GROUPS` (A à S), `RESERVED_GROUPS` (T à Z),
  `HISTORIC_GROUPS` (A à P), `HORS_CHAMP_GROUP` (S).
- *DGI Compliance Settings* : listes de groupes limitées à A à S (Tax Group Mapping, groupe par défaut,
  Matrice D). Nouveau paramètre **Opérations hors champ** : « Groupe S (spécifications v2) » par défaut, ou
  « Groupe A + code HORS CHAMP (communiqué n°010) ».
- *DGI Tax Group* : champ **Réservé (DGI)** ; un groupe T à Z reste inactif même si on le coche « Actif ».
- La Matrice D voit le groupe réel d'un article hors champ (S).

## 5. Mise à jour du site (`bench migrate`)

Le patch `v5_5.specifications_v2` est idempotent et ne supprime rien :

1. crée les groupes A à S manquants ; groupe A renommé « Exonere » s'il contenait « hors champ » (sauf
   verrouillage manuel) ;
2. crée ou marque T à Z réservés et inactifs ;
3. remplace un groupe par défaut T à Z par A ; règle hors champ = groupe S si elle n'a jamais été choisie ;
4. désactive les règles de Matrice D sur T à Z ; signale les lignes du Tax Group Mapping sur T à Z ;
5. inscrit le résultat au journal (*DGI Gateway Event*, `SPECS_V2`, niveau Alerte s'il reste une correction à
   faire).

## 6. Après la mise à jour

1. *DGI Compliance Settings* › **Tester la connexion** : l'e-MCF doit annoncer la version 2.x.
2. **Synchroniser TOUT** : taux de Q et R (8 %), S (0 %), T à Z réservés.
3. **Tax Group Mapping** : relier les modèles de taxe « TVA 8 % » au groupe Q (ou R pour un marché public à
   financement extérieur), et un modèle sans TVA au groupe S si besoin. Les articles cochés *Hors champ TVA (DGI)*
   passent seuls en S ; leur modèle ne doit pas porter de TVA.
4. Postes MCF : recharger la page *Poste MCF* (Ctrl+F5). Passerelles Python : installer la 0.3.0 sur chaque PC.
5. MCF physiques : la lecture du MCF doit afficher « protocole MCF-SFE v2 ». Sinon, demander au fournisseur la mise
   à jour ; en attendant, une facture avec un article en Q, R ou S est refusée sur ce MCF et doit être normalisée
   par l'e-MCF.

## 7. Homologation (Note de service n°01/220)

- **Ré-homologation obligatoire** après une modification des groupes de taxation (§ I.4 et VI.2), à demander dans
  les **30 jours ouvrables** qui suivent l'annonce de la DGI. Selon la date retenue pour l'annonce, cela donne
  environ le **25 novembre 2026** (depuis la note 209 du 14 octobre) ou le **7 décembre 2026** (depuis la note 220
  du 26 octobre). Calcul hors jours fériés, à confirmer auprès de la Commission.
- **Mise en production** d'une version soumise à ré-homologation : pas avant de l'avoir obtenue, sauf mesure
  transitoire de la DGI (§ VI.2). La 5.5.0 est donc à déployer d'abord sur le site de test pour la campagne.
- L'attestation ne couvre que les **types de factures et groupes présentés** pendant les essais (§ VI.2). Présenter
  les 19 groupes A à S, en particulier Q, R et S, sur MCF et sur e-MCF.
- Un SFE doit pouvoir **utiliser le MCF et l'e-MCF** (§ VI.2). Le tableau de conformité v2 retire en conséquence la
  mention « applicable uniquement au e-MCF » (1.4, 1.5, 1.23, 1.29). Le canal MCF de la HL2 y répond.
- Homologation transitoire : homologation complète dans les **3 mois** suivant le 26 octobre 2026, soit avant le
  26 janvier 2027 (§ I.3).
- Dépôt possible en ligne (plateforme e-Homologation). Formulaire : cocher « RE-HOMOLOGATION » avec le numéro
  d'attestation si le SFE est déjà homologué. Version logicielle : 5.5.0.

## 8. Tests

| Suite | Résultat |
|---|---|
| Passerelle (`dgi_gateway/tests`, dont 25 nouveaux dans `test_protocol_v2.py`) | 73/73 |
| Poste navigateur (`tests/js/dgi_mcf.test.js`, dont 4 nouveaux protocole v2 et parité ST2 avec la passerelle) | 18/19 ; l'échec (signature WebCrypto vérifiée par `gateway.auth`) exige Frappe dans l'interpréteur Python, comme avant |
| Calcul v2 sans base (`tests/test_specs_v2_calc.py`) | 6/6 |
| Intégration Frappe `tests/test_specs_v2_550.py` (V01 à V08) et `test_hl1_emcf` (CT29 adapté) | À exécuter sur un site de test : `bench --site <site> run-tests --module dgi_compliance.tests.test_specs_v2_550` |

La logique du mapper (hors champ en S, option communiqué n°010, refus du hors champ avec TVA, refus de T à Z) a été
exercée hors banc sur une version simulée de Frappe. La suite Frappe n'a pas été exécutée sur un banc.

## Fichiers

`edef/calc.py`, `edef/mapper.py`, `edef/matrix.py`, `edef/seed.py`, `edef/sync.py`, `edef/tasks.py`,
`fiscal/mcf_payload.py`, `fiscal/mcf_status.py`, `public/js/dgi_mcf.js`, `public/js/dgi_mcf_desk.js`,
page `dgi_mcf_station`, DocTypes *DGI Compliance Settings*, *DGI Tax Group*, *DGI Tax Group Map*,
*DGI Validation Matrix*, fixture `Item-dgi_hors_champ`, patch `patches/v5_5/specifications_v2.py`,
`dgi_gateway/` (`protocol.py`, `device.py`, `fiscalize.py`, `service.py`, `simulator.py`), tests.
