# dgi_compliance — HL v1.0.0

## Numérotation des versions (HL)

Le SFE suit une numérotation propre depuis le dossier d'homologation : **HL v1.0.0 = ancienne 5.5.2**.
Un seul numéro vaut pour tout le SFE homologué : application (`dgi_compliance`), passerelle (`dgi_gateway`)
et poste navigateur (`web-1.0.0`).

| Version | Contenu | Décision |
|---|---|---|
| HL v1.0.**x** | Corrections sans changement de fonctionnalité (anomalies, conformité, documents) | Proposée, validée par le responsable du produit |
| HL v1.**x**.0 | Changement important de fonctionnalité | Proposée, validée par le responsable du produit |
| HL v**2**.0.0 | Changement majeur | Décidée par le responsable du produit seul |

Étiquettes Git : `HLv1.0.0`, `HLv1.0.1`… Notes de version : `WHATS_NEW_HL<version>.md`. Les fichiers
`WHATS_NEW_1.4.md` à `WHATS_NEW_5.5.2.md` sont l'historique de l'ancienne numérotation.

Application Frappe indépendante qui met ERPNext v16 en conformité avec la **facture normalisée**
de la DGI (RDC), selon les **spécifications techniques v2** (octobre 2026). Elle normalise les factures de vente
par l'**e-MCF** (API e-MCF 2.0) et par le **MCF physique** (protocole MCF-SFE v2).
Elle est **upgrade-safe** : aucun fichier de Frappe ou d'ERPNext n'est modifié (hooks, champs
personnalisés en fixtures, DocTypes et formats d'impression propres, patches propres).

Fonctions principales :
- normalisation e-MCF à la soumission (verrou sans exception), reprise sans doublon, contrôle des
  montants avant confirmation, journal électronique ;
- groupes de taxation A–Z (FACNO v2.0), taxe spécifique, avoirs FA/EA (COR, RAN, RAM, RRR),
  export, acompte, devises (USD) au taux de change saisi sur la facture ;
- format **DGI Facture Normalisee** (toutes les mentions obligatoires, point de vente, équivalent
  CDF et montant en lettres en francs congolais) ;
- rapports **X, Z et A** (DocType *DGI Rapport Fiscal*) ;
- mode MCF physique (USB) hérité de la v4 — hors périmètre de l'homologation HL1.

Depuis la v5.1 (HL2) : MCF physique par poste navigateur ou passerelle, routage e-MCF / MCF avec secours.
v5.4.0 : deux utilisateurs suffisent (rôle DGI Approbateur) et référence séquentielle par point de vente
imprimée sous le numéro de facture (`WHATS_NEW_5.4.0.md`). v5.4.1 : revue de la passerelle, 16 anomalies
corrigées (`WHATS_NEW_5.4.1.md`). v5.5.0 : spécifications techniques v2 de la DGI (octobre 2026) — groupes A à S,
T à Z réservés, hors champ en groupe S, API e-MCF 2.0 (`trq`…`trz`), protocole MCF-SFE v2 (C1h et 33h avec `ST2`)
(`WHATS_NEW_5.5.0.md`). v5.5.1 : spécifications v2 strictes (hors champ toujours en groupe S) et
périmètre homologué de la Note de service n°01/220 (`WHATS_NEW_5.5.1.md`). v5.5.2 = **HL v1.0.0** : groupe N
(TVA spécifique) compté en TVA partout, contrôle des types d'article (`WHATS_NEW_5.5.2.md`, `WHATS_NEW_HL1.0.0.md`).

Documents : `WHATS_NEW_HL1.0.0.md`, puis `WHATS_NEW_5.5.2.md` à `WHATS_NEW_5.0.0.md` (nouveautés),
`GUIDE_DEPLOIEMENT_v5.0.5.md` (Frappe Cloud), `TESTS_v5.0.5.md` (rapport de tests). Historique : `WHATS_NEW_*.md`.
