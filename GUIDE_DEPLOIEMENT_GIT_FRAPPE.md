# Guide de déploiement pas-à-pas — Git → GitHub → Frappe Cloud (ERPNext v16)

Objectif : remplacer l'**ancienne version** de l'app par la version **1.1.0** mise à jour, la pousser
sur GitHub, puis la déployer/configurer sur votre ERPNext v16 (Frappe Cloud).

| Élément | Chemin |
|---|---|
| **Dossier mis à jour (source)** | `C:\Users\HP\Documents\Claude\Projects\ERPNext Updat\dgi_compliance` |
| **Dépôt GitHub local (cible)** | `C:\Users\HP\Documents\GitHub\dgi_compliance` |

Outils que vous avez : **GitHub Desktop**, **Git Bash**, **PowerShell**. Le guide donne les 3 ; suivez
**une seule** méthode par étape (la *recommandée* est indiquée).

> ⚠️ Convention de chemin : dans **Git Bash**, `C:\Users\HP\...` s'écrit `/c/Users/HP/...`.
> Le dossier source contient une **espace** (`ERPNext Updat`) → toujours mettre les chemins entre
> guillemets.

---

## ÉTAPE 0 — Prérequis et sauvegarde (5 min, à ne pas sauter)

1. **Fermez VS Code / l'explorateur** ouverts sur le dépôt pour éviter les verrous de fichiers.
2. **Sauvegarde Git de l'ancienne version** : on crée une branche de secours avant tout.
   - PowerShell ou Git Bash :
     ```bash
     cd "C:/Users/HP/Documents/GitHub/dgi_compliance"   # PowerShell accepte les / aussi
     git status                      # doit être "clean" ; sinon committez/écartez d'abord
     git checkout main               # ou 'master' selon votre repo
     git pull                        # récupère l'état distant
     git branch backup/old-version   # photo de l'ancienne version (pour rollback)
     ```
   - Vérifiez le nom de la branche principale : `git branch` (souvent `main`).
3. **Sauvegarde du site ERPNext** : dans Frappe Cloud → votre **Site** → onglet **Backups** →
   **Create Backup** (avant tout déploiement en production).

---

## ÉTAPE 1 — Comprendre la structure attendue du dépôt

Après mise à jour, la **racine du dépôt** `GitHub\dgi_compliance\` doit contenir :

```
dgi_compliance/                  <- racine du repo (= ce dossier .git)
├── pyproject.toml               <- version 1.1.0 + compat frappe v16
├── README.md
├── .gitignore
├── INSTALL_FRAPPE_CLOUD.md
├── TOKEN_MONITORING.md
├── GUIDE_DEPLOIEMENT_GIT_FRAPPE.md
└── dgi_compliance/              <- package Python
    ├── __init__.py              (__version__ = "1.1.0")
    ├── hooks.py
    ├── modules.txt
    ├── patches.txt
    ├── fixtures/custom_field.json
    ├── config/
    ├── edef/  (client.py, mapper.py, tasks.py)
    └── dgi_compliance/doctype/  (settings + dgi_payment_mode_map + dgi_tax_group_map)
```

> Le double dossier `dgi_compliance/dgi_compliance/` est **normal** pour une app Frappe (repo →
> package). Ne « corrigez » pas cette imbrication.

---

## ÉTAPE 2 — Apporter les fichiers à jour dans le dépôt local

On remplace proprement le contenu (en gardant `.git`) pour que les fichiers supprimés dans la
nouvelle version disparaissent aussi. **Choisissez UNE méthode.**

### Méthode A — PowerShell + robocopy (recommandée)
`robocopy /MIR` synchronise à l'identique et exclut `.git` et les caches Python.
```powershell
$src  = "C:\Users\HP\Documents\Claude\Projects\ERPNext Updat\dgi_compliance"
$repo = "C:\Users\HP\Documents\GitHub\dgi_compliance"

robocopy $src $repo /MIR /XD ".git" "__pycache__" /XF "*.pyc"
```
> ℹ️ `robocopy` renvoie un **code de sortie 0 à 7 = succès** (1 = fichiers copiés). Un code ≥ 8 = erreur.
> `/MIR` supprime dans la cible ce qui n'existe plus dans la source — d'où l'exclusion `/XD ".git"`
> qui **protège l'historique Git**.

### Méthode B — Git Bash
```bash
src="/c/Users/HP/Documents/Claude/Projects/ERPNext Updat/dgi_compliance"
repo="/c/Users/HP/Documents/GitHub/dgi_compliance"

# 1) vider le dépôt SAUF .git
find "$repo" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
# 2) copier la nouvelle version
cp -a "$src/." "$repo/"
# 3) retirer les caches Python s'il y en a
find "$repo" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$repo" -name '*.pyc' -delete 2>/dev/null
```

### Méthode C — Explorateur Windows + GitHub Desktop
1. Ouvrez les deux dossiers côte à côte.
2. Dans le dépôt, **sélectionnez tout sauf le dossier `.git`** et supprimez.
3. Dans la source, copiez **tout le contenu** (pas le dossier parent) et collez dans le dépôt.
4. Supprimez à la main les éventuels dossiers `__pycache__`.
5. La suite (commit) se fait dans GitHub Desktop (Étape 4, Méthode C).

---

## ÉTAPE 3 — Vérifier avant de committer

Dans Git Bash ou PowerShell, à la racine du dépôt :
```bash
cd "C:/Users/HP/Documents/GitHub/dgi_compliance"
git status              # liste fichiers modifiés / ajoutés / supprimés
git add -A              # met en index TOUT (ajouts, modifs ET suppressions)
git status              # re-vérifiez : la colonne doit refléter la nouvelle arborescence
git diff --cached --stat   # résumé des changements indexés
```
Points à contrôler :
- `pyproject.toml` apparaît modifié (version + `[tool.bench.frappe-dependencies]`).
- `dgi_compliance/__init__.py` contient `__version__ = "1.1.0"`.
- Aucun `__pycache__/` ni `*.pyc` dans la liste (sinon : `git rm -r --cached **/__pycache__`).

> Si l'ancienne version était **déjà** étiquetée `1.1.0`, incrémentez pour éviter toute confusion :
> mettez `1.1.1` dans `dgi_compliance/__init__.py` **et** `pyproject.toml`, puis refaites `git add -A`.

---

## ÉTAPE 4 — Committer et pousser sur GitHub

### Méthode A — Git Bash (recommandée)
```bash
git commit -m "feat: dgi_compliance v1.1.0 — e-DEF flow, mapper ERPNext, scheduler jeton"
git push origin main          # remplacez 'main' par votre branche principale
```

### Méthode B — PowerShell (mêmes commandes git)
```powershell
git commit -m "feat: dgi_compliance v1.1.0 - e-DEF flow, mapper ERPNext, scheduler jeton"
git push origin main
```

### Méthode C — GitHub Desktop
1. Ouvrez GitHub Desktop → le dépôt `dgi_compliance` est listé à gauche.
2. Le panneau central montre tous les changements (ajouts/suppressions).
3. En bas à gauche : **Summary** = `dgi_compliance v1.1.0`, puis **Commit to main**.
4. En haut : **Push origin**.

### (Optionnel mais conseillé) Étiqueter la version
```bash
git tag -a v1.1.0 -m "DGI Compliance 1.1.0"
git push origin v1.1.0
```

✅ Le code est maintenant sur GitHub. Vérifiez sur github.com que le dernier commit et l'arborescence
sont corrects.

---

## ÉTAPE 5 — Frappe Cloud : autoriser l'accès au dépôt privé (une seule fois)

Frappe Cloud déploie depuis GitHub via son **GitHub App**. Pour un repo **privé** :
1. Allez sur **https://github.com/settings/installations**.
2. À côté de **Frappe Cloud**, cliquez **Configure**.
3. Section *Repository access* → **Only select repositories** → ajoutez **`dgi_compliance`** (ou
   *All repositories*) → **Save**.

Réf. doc : *How to install a custom app* (docs.frappe.io/cloud/benches/custom-app).

---

## ÉTAPE 6 — Frappe Cloud : déployer la nouvelle version

> Sur Frappe Cloud, une app vit dans un **Bench (Bench Group)**, et un **Site** s'installe dessus.
> Deux cas selon que l'app est déjà installée ou non.

### Cas 1 — L'app est DÉJÀ installée (mise à jour de l'ancienne version) ✅ votre cas
1. Dashboard Frappe Cloud → **Benches** → ouvrez votre **Bench Group**.
2. Onglet **Apps** → ligne **dgi_compliance**. Frappe Cloud détecte le nouveau commit (sinon cliquez
   l'action de rafraîchissement / **Fetch latest** de l'app).
3. Sélectionnez le **nouveau commit** (ou la branche `main`) comme cible de l'app.
4. Cliquez **Update** / **Deploy** : un **nouveau déploiement (deploy candidate)** est construit
   (build de l'image). Suivez la progression dans l'onglet de déploiement.
5. À la fin du build, Frappe Cloud **met à jour les sites** du bench. La migration
   (`bench migrate`) s'exécute **automatiquement** : vos **Custom Fields** (fixtures) et **DocTypes**
   (Settings + tables de mapping) sont créés/mis à jour.

Réf. doc : *Updating a Bench* (docs.frappe.io/cloud/benches/updating_a_bench) et *Update an app/site
on a private bench* (docs.frappe.io/cloud/sites/how-to-update-an-app-site-on-a-private-bench).

### Cas 2 — L'app n'est PAS encore sur le bench (première installation)
1. Bench Group → **Apps** → **Add App** → onglet **GitHub** → sélectionnez le repo `dgi_compliance`
   + branche `main`. (Frappe Cloud lit `pyproject.toml` et vérifie la compat frappe v16.)
2. **Deploy** le bench.
3. Puis allez sur le **Site** → **Apps** → **Install** → **dgi_compliance**.

> 🔧 Si le build échoue, voir l'Étape 9 (dépannage) — souvent `pyproject.toml` ou la version frappe.

---

## ÉTAPE 7 — Vérifier que la migration a bien pris

Après déploiement, ouvrez votre site ERPNext (Desk) :
1. **Awesomebar** → tapez **DGI Compliance Settings** : la page doit s'ouvrir (DocType présent).
2. Ouvrez une **Sales Invoice** existante → la section **« Normalisation DGI (e-MCF) »** doit
   apparaître (champs : Statut DGI, DGI UID, Code DEF/DGI, Compteurs, NIM, Date/heure, QR, Erreur).
3. Si la section n'apparaît pas : Awesomebar → **Customize Form** → DocType *Sales Invoice* → vérifiez
   les Custom Fields `custom_dgi_*`. En dernier recours, réappliquez les fixtures (Étape 9).

---

## ÉTAPE 8 — Configuration finale dans ERPNext

Ouvrez **DGI Compliance Settings** :

1. **Enabled** : cochez.
2. **Environment** : `Test` (puis `Production` une fois validé).
3. **Base URL (Test)** = `https://developper.dgirdc.cd/edef` ; **(Production)** = `https://edef.dgirdc.cd`.
4. **e-DEF JWT Token** : collez le jeton du portail e-MCF.
5. **ISF** = votre identifiant SFE `AAA-BBB-NN`. **Seller NIF** : vide ⇒ pris depuis `Company.tax_id`.
6. **Auto-normalize on Submit** : coché (crée + confirme).
7. **Token Monitoring** :
   - **Check Frequency** = `Daily` / `Weekly` / `Monthly` ; renseignez **Weekday** ou **Day of Month**
     selon le choix ; **Warn N Days Before** = 7 ; **Notify Recipients** = vos e-mails (séparés par `,`).
8. **Mappings** :
   - **Payment Mode Mapping** : pour chaque *Mode of Payment* ERPNext, choisissez le type e-DEF
     (`Espèces`→`ESPECES`, `Mobile Money`→`MOBILEMONEY`, …).
   - **Tax Group Mapping** : reliez chaque *Item Tax Template* (ou un taux) au groupe e-DEF `A`–`P`.
     👉 Récupérez les valeurs exactes des groupes via Swagger : `GET /api/info/taxGroups`.
9. **Save**.

> 📧 Pour que les alertes de jeton partent, vérifiez qu'un **compte e-mail sortant** est configuré
> (Settings → Email Account, ou Frappe Cloud Mail).

---

## ÉTAPE 9 — Test de bout en bout + déclenchement manuel du scheduler

### A. Tester une facture
1. Settings en `Test`, `Enabled` coché, jeton de test collé.
2. Créez et **Submit** une *Sales Invoice* simple (1 article, groupe de taxe mappé).
3. Résultat attendu : **Statut DGI = Normalized**, **Code DEF/DGI** et **QR Code** remplis.
4. En cas d'échec : **Statut = Error**, le message est dans **Erreur DGI**, et le détail
   requête/réponse dans **Error Log** (Awesomebar → *Error Log*, filtre `dgi_compliance.edef`).

### B. Tester la surveillance du jeton sans attendre le cron
Méthode sans SSH (UI) :
1. Awesomebar → **Scheduled Job Type**.
2. Trouvez l'entrée `dgi_compliance.edef.tasks.check_token_expiry` → ouvrez-la → **Execute Now**.
3. Vérifiez **Token Valid Until** mis à jour dans Settings, et vos e-mails / **Error Log** (`[DGI] …`).

Alternative (si *Server Script* activé) : Awesomebar → **System Console** →
```python
from dgi_compliance.edef.tasks import check_token_expiry
check_token_expiry()
```

---

## ÉTAPE 10 — Dépannage

### Git / GitHub
| Symptôme | Cause / solution |
|---|---|
| `error: failed to push ... rejected (non-fast-forward)` | Le distant a des commits que vous n'avez pas. `git pull --rebase origin main` puis `git push`. |
| Des `__pycache__`/`*.pyc` apparaissent dans le commit | `git rm -r --cached **/__pycache__` puis commit ; le `.gitignore` les exclut ensuite. |
| Mauvaise branche | `git branch` pour voir, `git checkout main` pour basculer. |
| Rollback total | `git checkout backup/old-version` (branche créée à l'Étape 0). |

### Frappe Cloud (build / déploiement)
| Symptôme | Cause / solution (page doc) |
|---|---|
| **Invalid pyproject.toml** | Vérifiez la syntaxe + `[tool.bench.frappe-dependencies]`. (common-issues/invalid-pyprojecttoml-file) |
| **Incompatible app version** | La contrainte frappe doit englober v16 (`>=16.0.0,<17.0.0`). (common-issues/incompatible-app-version) |
| **Required app not found** | `required_apps = ["erpnext"]` est dans `hooks.py` ✅ ; assurez-vous qu'erpnext est sur le bench. (common-issues/required-app-not-found) |
| **Build might fail** | Consultez les logs de déploiement de Frappe Cloud. (common-issues/build-might-fail) |
| Repo privé non visible | Reconfigurez la **Frappe Cloud GitHub App** (Étape 5). |
| Custom Fields absents après deploy | Exécutez la migration : Site → **Migrate** ; ou réimportez les fixtures (voir ci-dessous). |

Réappliquer les fixtures manuellement (si SSH activé) :
```bash
bench --site <votre-site> migrate
bench --site <votre-site> reload-doc dgi_compliance dgi_compliance dgi_compliance_settings
```

---

## ANNEXE — Variante self-hosted (bench, hors Frappe Cloud)
Si un jour vous gérez votre propre bench :
```bash
cd /home/frappe/frappe-bench
bench get-app dgi_compliance https://github.com/<vous>/dgi_compliance --branch main
bench --site <votre-site> install-app dgi_compliance     # 1re install
# mise à jour ultérieure :
cd apps/dgi_compliance && git pull && cd ../..
bench --site <votre-site> migrate
bench build && bench restart
```

---

### Récapitulatif express
1. Sauvegarde (branche Git + backup site).  2. Copier la v1.1.0 dans le repo (robocopy `/MIR`).
3. `git add -A` → commit → push (+ tag).  4. Autoriser le repo dans la Frappe Cloud GitHub App.
5. Bench → Apps → **Update/Deploy**.  6. Migration auto.  7. Configurer **DGI Compliance Settings**.
8. Tester une facture + **Execute Now** sur le scheduled job.
