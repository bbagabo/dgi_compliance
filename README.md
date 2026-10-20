# dgi_compliance — v5.0.4 (HL1)

Application Frappe indépendante qui met ERPNext v16 en conformité avec la **facture normalisée**
de la DGI (RDC) et normalise les factures de vente via l'**e-MCF** (API e-MCF v1.3).
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

Documents : `WHATS_NEW_5.0.4.md` à `WHATS_NEW_5.0.0.md` (nouveautés),
`GUIDE_DEPLOIEMENT_v5.0.4.md` (Frappe Cloud), `TESTS_v5.0.4.md` (rapport de tests). Historique : `WHATS_NEW_*.md`.
