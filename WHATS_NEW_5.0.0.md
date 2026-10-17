# DGI Compliance v5.0.0 (HL1) — Notes de version

Version de mise en conformité pour l'homologation transitoire du SFE (e-MCF par API).
Base : v4.0.0. Le mode MCF physique (USB) de la v4 est conservé mais hors périmètre (phase 2).

## 1. Intégrité fiscale (bloquant / critique)

- **Aucune facture sans éléments de sécurité** (SFE 1.1.5) : la soumission exige la normalisation
  e-MCF, sans exception. La case « Override validation DGI » ne concerne plus que les matrices de
  validation ; l'option « Verrouillage postage = Off » est supprimée.
- **Normalisation dans une transaction propre** : l'UID, le Code DEF/DGI et le journal sont
  enregistrés immédiatement ; un échec ultérieur de la soumission ne les efface plus.
- **Facture créée et soumise en un seul appel (POS)** : la normalisation a lieu après les écritures
  comptables, dans la même transaction ; tout échec annule l'ensemble (aucune facture orpheline à la DGI).
- **Reprise sans doublon** : avant tout envoi, les demandes en attente de l'e-MCF sont consultées
  (réponse perdue, erreur 63) ; une confirmation sans réponse est signalée « CONFIRMATION
  INCERTAINE » et bloque tout renvoi jusqu'à vérification sur le portail DGI.
- **Contrôle des montants avant CONFIRM** : le SFE recalcule les totaux (règles SFE 1.5) et les
  compare à ceux de l'e-MCF, total et groupe par groupe ; en cas d'écart, la demande est annulée.
- **Facture normalisée figée** : modification du contenu fiscal, suppression et annulation ERPNext
  refusées. La correction passe par une facture d'avoir (FA/EA, nature RAN, RAM, COR ou RRR).
- **Champs fiscaux protégés** après soumission (Code DEF, QR, compteurs...).

## 2. e-MCF complet

- Mode de prix déduit de la facture (TTC si taxes incluses dans le prix, HT sinon).
- Prix construits sur le montant net de ligne : remises de ligne **et** remise globale prises en
  compte ; prix arrondis à 2 décimales, quantités à 3 (SFE 1.5.1/1.5.2).
- **Groupes de taxation A à Z** (FACNO v2.0 : Q 8 %, R 8 % crédit d'impôt, S hors champ) ;
  articles « hors champ » transmis avec le code `HORS CHAMP` (communiqué n°010).
- **Taxe spécifique** (`taxSpecificValue` / `taxSpecificAmount`) : montant par unité en CDF ou
  pourcentage, saisi sur l'article, inclus dans le prix HT (base TVA augmentée, SFE 1.5.10).
- **Paiements** : le mode réellement choisi sur la facture est transmis et imprimé ; le mode par
  défaut ne sert que si aucun mode n'est sélectionné. Ordre : paiements POS → champ « Mode de
  paiement » → échéancier → défaut. Somme des paiements = total facture (solde en CREDIT).
- `PaymentDto` : champs `currencyCode` / `currencyRate` conformes à la spécification.
- Opérateur = utilisateur qui normalise (nom complet + identifiant).
- Avoirs : référence = Code DEF/DGI de la facture d'origine (ou `RRR`), nature obligatoire,
  prix unitaire identique à l'origine pour RAN/RAM, origine obligatoirement normalisée.
- Commentaire A obligatoire pour un client AO ou un article du groupe D (référence d'exonération,
  imprimée « Réf. Exo. »).
- Contrôle du taux de change USD : ±1 % du taux de référence publié par l'e-MCF (SFE 1.1.30).
- Lignes et factures à montant nul ou négatif refusées (SFE 1.1.21/1.1.22).
- Messages d'erreur e-MCF traduits (table des codes 1 à 99), jeton invalide/expiré (HTTP 401) explicite.
- Casse de l'action CONFIRM/CANCEL paramétrable (minuscules par défaut, comme la v4 validée en test).

## 3. Nouvelle facture normalisée « DGI Facture Normalisee » (format par défaut)

- En-tête société : logo, adresse, e-mail, téléphone, NIF, RCCM, Id. Nat., référence ISF.
- Bloc **Point de vente** (code magasin, nom, adresse, ville, téléphone, référence NID, terminal),
  alimenté automatiquement depuis l'e-MCF (`/api/info/status`) ou le POS Profile.
- **Informations générales** : n°, commande, date **et heure**, échéance, **terme de paiement**,
  **mode de paiement**, **type de facture DGI**, devise, point de vente, utilisateur (nom + ID),
  date de création, version ORIGINAL / DUPLICATA.
- Mentions FACTURE D'AVOIR + nature + référence d'origine, EXPORTATION, D'ACOMPTE.
- Lignes : code, désignation, type [BIE]/[SER]/[TAX], quantité, unité, prix, remise, montant,
  groupe (A-EX, B…), taux ; ligne T.S. pour la taxe spécifique.
- Ventilation par groupe de taxation, règlement (modes et montants), nombre d'articles.
- **Factures en USD** : montants commerciaux en USD ; **équivalent en CDF sous le Net à payer TTC**
  (valeurs de l'e-MCF) avec le taux ; **montant en lettres toujours en francs congolais**
  (ex. « Deux millions neuf cent cinquante mille francs congolais »).
- Éléments de sécurité : QR, Code DEF/DGI, DEF NID, compteurs, heure.
- L'ancien format « DGI Sales Invoice » reste disponible (retour arrière).

## 4. Rapports X, Z et A

- Nouveau DocType **DGI Rapport Fiscal** : X quotidien (depuis le dernier Z), X périodique,
  Z (numéroté, clôture la période), A (par article, depuis le dernier A).
- Contenu SFE 1.3/1.4 : totaux par type de facture, par groupe et type, par type de paiement,
  réductions, avoirs par nature, ventes incomplètes ; rapport A avec quantités vendues/retournées
  et stock.
- Chaque rapport est figé, non modifiable, non supprimable et journalisé.
- Boutons « Générer » dans la liste DGI Rapport Fiscal ; format d'impression dédié.

## 5. Journal électronique et sécurité

- Les échanges fiscaux du journal (création, confirmation, annulation, rapports) ne sont plus
  jamais purgés ; seules les entrées techniques le sont. Suppression réservée au System Manager.
- Réponse complète de l'e-MCF stockée sur la facture (impression = données échangées).

## 6. Paramétrage et migration (patch `v5_0.hl1_migrate`, idempotent)

- Groupes A–S selon FACNO v2.0 (taux et libellés, sans écraser une valeur synchronisée/manuelle).
- Types de client alignés sur l'annexe SFE (PM : dénomination + NIF…), uniquement s'ils n'ont pas
  été modifiés ; PC et AO ajoutés à la correspondance Customer Type.
- Matrices : avoirs sur biens autorisés (lignes d'origine non modifiées seulement).
- Date de normalisation renseignée pour les factures existantes (rapports).
- Format d'impression par défaut : « DGI Facture Normalisee » (Property Setter).
- Correction : l'installation sur un site neuf échouait en v4 (ISF obligatoire) — corrigé.

## 7. Tests

- 49 tests d'intégration Frappe/ERPNext v16 (e-MCF simulé) + 9 tests unitaires + 12 tests agent MCF.
- Voir `TESTS_v5.0.0.md`.
