# Installation sur Frappe Cloud (ERPNext v16)

`dgi_compliance` est une **app personnalisée upgrade-safe** : elle n'ajoute que des Custom Fields et
ses propres DocTypes. Aucun fichier du core ERPNext/Frappe n'est modifié, donc les montées de version
v16 et les `bench update` restent propres.

## 1. Publier l'app dans un dépôt Git
Frappe Cloud installe les apps depuis Git. Poussez ce dossier `dgi_compliance/` sur un repo
(GitHub/GitLab) privé. Structure attendue (déjà en place) :

```
dgi_compliance/                 <- racine du repo
├── pyproject.toml
└── dgi_compliance/             <- package Python
    ├── __init__.py  (__version__ = "1.1.0")
    ├── hooks.py
    ├── modules.txt
    ├── fixtures/custom_field.json
    ├── config/
    ├── edef/  (client.py, mapper.py, tasks.py)
    └── dgi_compliance/doctype/  (settings + child maps)
```

## 2. Ajouter l'app au Bench (Frappe Cloud)
Dans le tableau de bord Frappe Cloud :
1. **Apps → Add App → From GitHub** : sélectionnez le repo, branche `main`, version ERPNext **v16**.
2. Lancez le build. Une fois disponible, **installez l'app sur votre site**
   (Site → Apps → Install `dgi_compliance`).
3. Frappe exécute `bench migrate` : les Custom Fields (fixtures) et les DocTypes sont créés
   automatiquement.

> Variante self-hosted / bench local :
> ```bash
> bench get-app dgi_compliance <git-url>
> bench --site <site> install-app dgi_compliance
> bench --site <site> migrate
> ```

## 3. Configurer
**DGI Compliance Settings** (Awesomebar → « DGI Compliance Settings ») :
- **Enabled** : cochez pour activer le traitement.
- **Environment** : `Test` puis `Production`.
- **e-DEF JWT Token** : collez le jeton du portail e-MCF (champ chiffré).
- **ISF** : votre identifiant SFE `AAA-BBB-NN`.
- **Seller NIF** : laissez vide pour utiliser `Company.tax_id`, ou forcez une valeur.
- **Auto-normalize on Submit** : coché = crée + confirme. Décoché = crée seulement (Pending).
- **Mappings** :
  - *Payment Mode Mapping* : reliez chaque `Mode of Payment` ERPNext à un type e-DEF
    (`Espèces`→`ESPECES`, `Mobile Money`→`MOBILEMONEY`, …). Défaut = `ESPECES`.
  - *Tax Group Mapping* : reliez chaque `Item Tax Template` (ou un taux) au groupe e-DEF `A`–`P`.
    Récupérez les valeurs exactes via `GET /api/info/taxGroups` (Swagger). Défaut = `A`.

## 4. Vérifier les Custom Fields
Une fiche **Sales Invoice** doit afficher la section *Normalisation DGI (e-MCF)* avec :
Statut DGI, DGI UID, Code DEF/DGI, Compteurs, NIM, Date/heure, QR Code, Erreur. Tous en lecture seule.

## 5. Test de bout en bout
1. Réglez Environment = `Test`, collez un jeton de test, cochez Enabled.
2. Créez et **soumettez** une Sales Invoice simple (1 article, groupe de taxe mappé).
3. Le `on_submit` appelle e-DEF : *Statut DGI* doit passer à **Normalized** et *Code DEF/DGI*,
   *QR Code* se remplir. En cas d'échec, *Statut* = **Error** et *Erreur DGI* contient le message.
4. Consultez l'audit : **Error Log** filtré sur `dgi_compliance.edef[...]` (request/response de chaque appel).

## 6. Mise en production
- Passez Environment = `Production`, remplacez le jeton, sauvegardez.
- Refaites un test réel contrôlé.
- Voir `TOKEN_MONITORING.md` pour la surveillance d'expiration du jeton.

## Notes upgrade-safe
- Les Custom Fields sont livrés en **fixtures** : réexportez après modif avec
  `bench --site <site> export-fixtures --app dgi_compliance`.
- Le QR/Code DEF stockés en Custom Fields survivent aux mises à niveau.
- Pour afficher le QR sur le PDF de facture, ajoutez un **Print Format** personnalisé lisant
  `custom_dgi_qr_code` / `custom_dgi_code_def` (n'éditez pas le format standard).
