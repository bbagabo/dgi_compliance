# DGI Compliance 5.5.1 - Spécifications v2 strictes et périmètre homologué

19 novembre 2026, sur `main` (à partir de v5.5.0). Passerelle `dgi_gateway` 0.3.0 et poste navigateur `web-0.3.0`
inchangés.

Contexte : le SFE n'a encore aucune homologation. Il sera présenté en **homologation initiale** directement sur
les spécifications v2 (Note de service n°01/209 du 14 octobre 2026). La procédure applicable est celle de la
Note de service n°01/220 du 26 octobre 2026. La ré-homologation ne le concerne pas.

## 1. Hors champ : groupe S uniquement

| v5.5.0 | v5.5.1 |
|---|---|
| Groupe S par défaut ; option « Groupe A + code HORS CHAMP (communiqué n°010) » | Groupe S toujours, avec le vrai code article. Le paramètre *Opérations hors champ* est retiré. |

- La règle du communiqué n°010 (FACNO v1.2) est remplacée par FACNO v2.0 : le groupe A est réservé aux
  opérations exonérées, le groupe S aux opérations hors champ.
- Une valeur laissée par la 5.5.0 dans les paramètres est ignorée.
- Inchangé : un article coché *Hors champ TVA (DGI)* dont le modèle de taxe porte de la TVA est refusé.
- La compatibilité avec un MCF ou un e-MCF pas encore mis à jour reste en place. Elle ne change rien aux règles
  fiscales : elle refuse Q, R et S proprement au lieu de mal normaliser.

## 2. Périmètre homologué (Note n°01/220, § VI.2)

L'attestation de conformité couvre une version, des types de facture et des groupes de taxation précis. Le SFE
« ne peut être utilisé que pour les types de factures et les groupes de taxation expressément couverts par son
homologation ».

Nouvelle section *DGI Compliance Settings* › **Homologation DGI** :

| Champ | Rôle |
|---|---|
| Statut d'homologation du SFE | « Non homologue (essais) » (par défaut) ou « Homologue » |
| N° et date de l'attestation | Obligatoires quand le statut est « Homologue » |
| Version homologuée | Alerte si la version installée est différente (une modification fiscale demande une ré-homologation) |
| Groupes de taxation homologués | Lettres couvertes, par défaut `ABCDEFGHIJKLMNOPQRS` ; T à Z refusés |
| Types de facture homologués | Par défaut `FV,FT,FA,EV,ET,EA` |

- **Avant l'homologation**, aucune restriction : la campagne d'essais peut présenter tous les groupes et tous les
  types.
- **Une fois homologué**, une facture hors périmètre est refusée avant tout envoi, sur l'e-MCF comme sur le MCF.
  Exemple : « groupe(s) Q non couvert(s) par l'attestation d'homologation (groupes homologués : ABS) ; une extension
  demande une re-homologation ».
- Le contrôle ne s'applique qu'à la normalisation. Une facture déjà normalisée se réimprime et entre dans les
  rapports X, Z et A comme avant.

Fichiers : `edef/homologation.py` (nouveau, sans base de données), `edef/mapper.py`
(`homologation_scope_errors`), `edef/tasks.py`, `fiscal/mcf_payload.py`, DocType *DGI Compliance Settings*.

## 3. Homologation initiale sur les spécifications v2

| Point | Règle (Note n°01/220) |
|---|---|
| Type de procédure | Homologation complète. Aucune nouvelle homologation transitoire depuis le 26 octobre 2026 (§ I.3). |
| Formulaire | Cocher « HOMOLOGATION » (pas « RE-HOMOLOGATION »). Type de demande : FOURNISSEUR, CONTRIBUABLE ou GROUPE. Version du SFE : 5.5.1. |
| Dossier (§ I.2 a, onglet 03 du tableau v2) | a) formulaire ; b) RCCM ; c) quitus fiscal ; d) CNSS ; e) Id. Nat. ; f) catalogue ; g.1) manuel d'utilisation ; g.2) manuel de contrôle ; g.3) brochure ; g.4) tableau de conformité ; g.5) cas de test ; h) fichiers d'installation ; i) engagement de démonstration. Les engagements j, k et l de la v1.2 disparaissent. Documents en français. |
| Tableau de conformité v2 | 96 exigences : 72 obligatoires (O), 24 facultatives (F). La catégorie « P » (homologation provisoire) et la mention « applicable uniquement au e-MCF » disparaissent. À remplir : colonnes E (OUI/NON/PART), F (référence de la documentation) et G (description courte). |
| Interconnexion | Le SFE doit fonctionner avec le MCF et avec l'e-MCF (§ VI.2). |
| Périmètre présenté | Seuls les types de facture et les groupes présentés et validés sont couverts (§ VI.2). Présenter les 6 types et les 19 groupes A à S, sur MCF (protocole v2) et sur e-MCF (API 2.0). |
| Vérification technique (§ II.2) | Dans l'environnement de test de la DGI, sur invitation, avec démonstration par le requérant. Résultats consignés dans un procès-verbal. |
| Délais | Dossier transmis à la Commission dans les 30 jours ouvrables qui suivent le dépôt (§ I.2 b). Dépôt physique ou en ligne (e-Homologation). |
| Après l'attestation | Saisir le statut, le n°, la date, la version, les groupes et les types dans *Homologation DGI*. |

## 4. Tests

| Suite | Résultat |
|---|---|
| `tests/test_homologation_scope.py` (nouveau, sans base) | 8/8 |
| `tests/test_specs_v2_calc.py` | 6/6 |
| Passerelle `dgi_gateway/tests` | 73/73 |
| Poste navigateur `tests/js/dgi_mcf.test.js` | 18/19 (échec préexistant : exige Frappe dans l'interpréteur Python) |
| Intégration Frappe `tests/test_specs_v2_550.py` : V02 (règle du communiqué n°010 retirée) et V09 (périmètre homologué) | À exécuter sur un site de test |

```
bench --site <site> run-tests --module dgi_compliance.tests.test_specs_v2_550
```

## 5. Mise à jour

`bench migrate`. Aucun nouveau patch. Le champ *Opérations hors champ* disparaît du formulaire. La section
*Homologation DGI* apparaît, au statut « Non homologue (essais) ».
