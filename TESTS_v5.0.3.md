# Rapport de tests — DGI Compliance v5.0.3 (HL1)

## 1. Environnement d'exécution

| Élément | Valeur |
|---|---|
| Frappe / ERPNext | 16.52.0 / 16.51.1 (branches version-16) |
| Python / MariaDB | 3.14.6 / 10.11 |
| Site | site de test dédié, société « DGI Test SARL » (CDF, RDC, exercice 2026) |
| e-MCF | **simulé** (`dgi_compliance/tests/emcf_simulator.py`) : aucun appel réel à la DGI |
| Date | 17/10/2026 (v5.0.0), campagne complète rejouée le 18/10/2026 (v5.0.1) le 19/10/2026 (v5.0.2) et le 20/10/2026 (v5.0.3) |

Le simulateur reproduit le comportement documenté de l'API e-MCF v1.3 : demandes en attente
(10 maximum, erreur 63), recalcul des montants par groupe, contrôles de CONFIRM (erreurs 50/51),
CANCEL, référence d'avoir, endpoints d'information, et pannes injectables (délai dépassé, HTTP 500,
HTTP 401, réponse non JSON, réponse perdue après traitement, montants falsifiés).
**Les tests contre le vrai e-MCF de test DGI restent à exécuter (section 4).**

## 2. Résultats

| Suite | Tests | Réussis | Échecs |
|---|---|---|---|
| `tests.test_hl1_emcf` (intégration Frappe/ERPNext) | 60 | 60 | 0 |
| `tests.test_calc_words` (unitaires) | 12 | 12 | 0 |
| `mcf_agent/test_mcf_agent.py` (agent MCF, hors périmètre, non-régression) | 12 | 12 | 0 |
| Installation sur site neuf (`bench new-site --install-app dgi_compliance`) | 1 | 1 | 0 |
| Mise à jour depuis une sauvegarde v4.0.0 (`restore` + `migrate`, patch v5_0) | 1 | 1 | 0 |

v5.0.1 : le test « taux hors ±1 % refusé » est remplacé par trois tests d'intégration :
- le taux de la facture est transmis ;
- la somme des paiements est égale au total fiscal, même avec un taux à décimales ;
- un écart de montants au-delà de la tolérance annule la demande d'une facture en USD.

Trois tests unitaires couvrent l'alignement des paiements sur le total fiscal.

Anomalie de la v4 détectée et corrigée pendant ces tests : l'installation sur un site neuf
échouait (ISF obligatoire lors de l'initialisation des paramètres).

## 3. Fiches des 26 scénarios

Colonnes : préconditions communes = paramètres DGI actifs, jeton, ISF, correspondances taxes et
paiements, client PM « CLIENT PM SARL » (NIF A1234567B). Preuves à conserver : PDF de la facture,
entrée du journal *DGI Exchange Log*, capture du portail de vérification DGI (campagne réelle).

| ID | Scénario | Données de test | Étapes | Résultat attendu | Test automatisé | Résultat obtenu | Statut |
|---|---|---|---|---|---|---|---|
| T01 | Facture standard | 3 × lait (B) 10 000 + 1 × farine (C) 45 000, HT | Créer, soumettre | FV normalisée, Code DEF, QR, NIM, opérateur, paiement ESPECES = total | test_T01_standard_invoice | Conforme | OK |
| T02 | Plusieurs taux | B, C, A, Q (8 %), O (1 %) | Soumettre | TVA par groupe B 3 200, Q 800, O 980, A 0 | test_T02_multiple_rates_and_new_groups | Conforme | OK |
| T03 | Remise ligne | 2 × lait −10 % | Soumettre | price 9 000, originalPrice 10 000, « Remise 10% » | test_T03_line_discount | Conforme | OK |
| T04 | Remise globale | −5 % sur net (HT) et sur total (TTC) ; TTC sans remise | Soumettre | Total e-MCF = total ERPNext ; prix TTC saisi conservé | test_T04_* (3) | Conforme | OK |
| T05 | Exonérée | 10 × eau (A) | Soumettre | vtotal = 0 | test_T05_exempt_invoice | Conforme | OK |
| T06 | Devise étrangère | USD au taux 2 850 ; USD au taux 2 350,50 (loin du taux e-MCF 2 850, ancienne règle « Manuel » 3 000 présente) ; USD au taux 2 830,75 (total ERPNext 2 416 045,12, total fiscal 2 416 045,13) ; montants e-MCF falsifiés (+150) | Soumettre, imprimer | curCode USD, **curRate = taux saisi sur la facture**, prix en CDF = prix USD × ce taux, paiement currencyCode/currencyRate identiques ; taux imprimé « 1 USD = 2 350.50 CDF » ; somme des paiements = total fiscal (2 416 045,13) ; écart de montants → CANCEL, facture non confirmée, non soumise | test_T06_* (4) | Conforme | OK |
| T07 | Paiement partiel | POS, espèces 4 000 / 11 600 | Soumettre | ESPECES 4 000 + CREDIT 7 600 | test_T07_partial_payment_pos | Conforme | OK |
| T08 | Payée intégralement | POS espèces + carte ; POS soumis en un appel ; échecs | Soumettre | Somme = total ; soumission directe normalisée ; aucun orphelin DGI si échec | test_T08_* (4) | Conforme | OK |
| T09 | À crédit / mode choisi | Mode « Wire Transfer » ; mode non mappé | Soumettre | VIREMENT transmis et imprimé ; mode non mappé bloquant | test_T09_* (2) | Conforme | OK |
| T10 | Avoir total | Retour complet, nature RAN | Soumettre l'avoir | FA, référence = Code DEF d'origine, montants positifs | test_T10_total_credit_note_ran | Conforme | OK |
| T11 | Avoir partiel | Retour 1 article, RAM ; prix modifié ; nature absente | Enregistrer / soumettre | Normalisé ; prix différent refusé ; nature obligatoire | test_T11_* (3) | Conforme | OK |
| T12 | Annulation | Annuler une facture normalisée | Cancel | Refusé, message « émettez un avoir RAN » | test_T12_cancel_normalized_invoice_blocked | Conforme | OK |
| T13 | Client sans NIF | « Jean Particulier » PP | Soumettre | client.type PP, pas de NIF | test_T13_customer_without_nif_pp | Conforme | OK |
| T14 | Client avec NIF | PC, PL ; PM sans NIF | Soumettre | Type et NIF transmis ; PM sans NIF bloqué | test_T14_* (2) | Conforme | OK |
| T15 | e-MCF indisponible | HTTP 500 sur création | Soumettre | Soumission bloquée, brouillon en Error, pas de Code DEF | test_T15_emcf_unavailable | Conforme | OK |
| T16 | Délai dépassé | Création traitée mais réponse perdue | Soumettre | Demande retrouvée dans la liste en attente, 1 seul envoi, 1 seule facture DGI | test_T16_timeout_with_lost_response_no_duplicate | Conforme | OK |
| T17 | Réponse invalide | Corps HTML au lieu de JSON | Soumettre | Error, soumission bloquée | test_T17_invalid_response | Conforme | OK |
| T18 | Erreur d'authentification | HTTP 401 | Soumettre | Message « jeton invalide ou expiré » | test_T18_authentication_error | Conforme | OK |
| T19 | Jeton manquant / expiré ⁽¹⁾ | Jeton vide | Soumettre | Message « Jeton manquant » | test_T19_missing_token | Conforme | OK |
| T20 | Envoi en double | Normaliser deux fois | Bouton ×2 | Un seul envoi, même Code DEF | test_T20_double_normalization | Conforme | OK |
| T21 | Nouvelle tentative | Délai dépassé puis succès ; confirmation perdue | Soumettre ×2 | Succès au 2e essai ; confirmation perdue = « CONFIRMATION INCERTAINE », aucun renvoi | test_T21_* (2) | Conforme | OK |
| T22 | Calcul / arrondi | Quantités 2,375 et 3,5 ; montants e-MCF falsifiés (+150) | Soumettre | Quantité à 3 décimales ; écart détecté → CANCEL, Error | test_T22_* (2) | Conforme | OK |
| T23 | Champ obligatoire absent | ISF vide ; prix 0 | Soumettre / enregistrer | Refus explicite | test_T23_* (2) | Conforme | OK |
| T24 | Utilisateur non autorisé | Utilisateur sans droit ; modification Code DEF ; brouillon normalisé modifié/supprimé ; override | Actions | PermissionError ; champs protégés ; brouillon figé et non supprimable ; override sans effet sur la normalisation | test_T24_* (4) | Conforme | OK |
| T25 | Impression et PDF | Facture CDF (mode Cash) et USD | Imprimer, PDF | Mentions obligatoires présentes, PDF généré ; USD : équivalent CDF, lettres en francs congolais | test_T25_print_cdf, test_T25_print_usd | Conforme | OK |
| T26 | Mise à jour Frappe/ERPNext | Restauration v4 + migrate ; site neuf | migrate | Champs, format par défaut et hooks présents ; aucune personnalisation du cœur | test_T26 + scénarios de déploiement | Conforme | OK |

⁽¹⁾ L'API e-MCF n'utilise pas de certificat client : l'authentification repose sur le jeton JWT.
Le scénario « certificat expiré ou incorrect » est donc couvert par T18 et T19.

v5.0.3 : tests V01 à V07.

| ID | Scénario | Résultat attendu | Statut |
|---|---|---|---|
| V01 | Ancien champ client `dgi_client_type` (v1) présent avec la valeur PM | Champ supprimé, valeur reprise dans `dgi_customer_type` | OK |
| V02 | Matrice C renommée | « C - Invoice Type / Pays (adresse client) », plus aucune ligne avec l'ancien nom | OK |
| V03 | Client avec adresse principale en France, aucune adresse choisie sur la facture | Pays lu = France, Non-CD | OK |
| V04 | Facture à l'exportation pour ce client | EV normalisée | OK |
| V05 | Facture FV pour ce client | Bloquée, message citant le pays « France » | OK |
| V06 | Facture à l'exportation pour un client dont l'adresse est en RDC | Bloquée (le pays de la société n'est plus utilisé) | OK |
| V07 | Client sans adresse | Considéré comme local (CD) | OK |

Cas DGI également automatisés : CT8 (taxe spécifique), CT13 (avoir RRR sans facture), CT14
(client AO + groupe D + référence d'exonération), CT15 (exportation), CT29 (hors champ),
R01–R03 (rapports X, Z, A, numérotation, immuabilité).

v5.0.2 : test R04 (impression des rapports). Il vérifie :
- le format par défaut du DocType est « DGI Rapport Fiscal » ;
- aucune donnée JSON n'est imprimée ;
- toutes les sections exigées par SFE 1.3.3 et 1.4.2 sont présentes ;
- les groupes A à S sont listés pour chaque type de facture ;
- les montants sont au format 1.234.567,89.

## 4. Campagne à exécuter sur l'environnement de test DGI (dossier d'homologation)

À dérouler sur un site de test relié à `https://developper.dgirdc.cd/edef`. Chaque facture est
ensuite vérifiée sur `https://developper.dgirdc.cd/sygdef/verification`. La photo de la facture
imprimée est insérée dans `SFE_cas_de_test_v1.2.docx`. Si les prix TTC et HT sont pris en charge,
il faut deux factures par cas : modèle « TVA incluse » et modèle « TVA en sus ».

| Cas | Paramétrage ERPNext | Points à contrôler |
|---|---|---|
| 1–7 | FV, articles B/C/A, clients PM, PP, PC, PL ; cas 4 : quantités 2,375 et 3,5 (unité fractionnable) | Type de client [PM]…, NIF, groupes, totaux |
| 8 | Articles avec *Taxe spécifique DGI* (ex. 230) | Ligne T.S. imprimée ; ts sur le portail |
| 9 | Type de facture DGI = FT | Mention D'ACOMPTE |
| 10–12 | Retour sur la facture du cas 1 (RAN), 3 (RAM), 6 (COR) | Référence = Code DEF d'origine ; nature imprimée |
| 13 | Avoir sans « Retour contre », nature RRR | Référence « RRR » |
| 14 | Client AO, articles groupe D, Commentaire A = référence d'exonération | « Réf. Exo. » imprimé |
| 15–17 | Client dont l'adresse est hors RDC, « Facture à l'exportation » cochée, groupe E (EV, EA sur 15, ET) | Mention EXPORTATION ; Matrice C : pays de l'adresse du client |
| 18 | Groupes F et G | Taux 16 % et 5 % |
| 19–24 | Groupes H, I, J, K, L, M + N (articles TAX pour L et N) | Groupes et types d'article |
| 25 | Remise 10 % (B), remise de 300 CDF (A) | originalPrice, « Remise » |
| 26 | Facture en USD (taux saisi sur la facture) | Équivalent CDF sous le Net à payer, taux imprimé = taux saisi = taux affiché par le portail DGI, lettres en francs congolais |
| 27–28 | Groupes O (1 %) et P | Taux 1 % |
| 29 | Article coché *Hors champ TVA (DGI)* (groupe A) | Code d'article « HORS CHAMP » |
| R01–R03 | DGI Rapport Fiscal → X quotidien, Z, A → *Imprimer* (format par défaut) | Tous les éléments du rapport (SFE 1.3.3 a–m, 1.4.2 a–f), mise en page du rapport e-UF, aucune donnée JSON |

## 5. Anomalies restantes / points à confirmer avec la DGI

- Traitement de la taxe spécifique en mode TTC (TS augmentée de la TVA) : à confirmer par le cas 8 en TTC.
- Acceptation par l'API v1.3 des groupes Q à Z (FACNO v2.0) et position du « hors champ » (A + code
  « HORS CHAMP » selon le communiqué n°010, ou groupe S selon FACNO v2.0).
- Casse CONFIRM/CANCEL : minuscules conservées, car validées sur l'environnement de test DGI avec la v4.
- Domaine de test `developper.dgirdc.cd` (spécification et cas de test) ou `developper.dgirep.cd` (communiqué).
- Déclaration des paiements partiels (part payée + CREDIT pour le solde).
