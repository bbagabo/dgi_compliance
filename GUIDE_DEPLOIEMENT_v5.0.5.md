# Guide de déploiement — DGI Compliance v5.0.5 (HL1)

Architecture : Frappe Cloud, application installée depuis le dépôt GitHub `bbagabo/dgi_compliance`.
Aucun fichier de Frappe ou d'ERPNext n'est modifié : la mise à jour se fait par un simple
déploiement de l'application puis la migration du site.

## 1. Avant la mise à jour

1. Frappe Cloud → **Site** → *Backups* → **Schedule Backup** (cocher *Include files*).
2. Noter la version actuelle : *Apps* → dgi_compliance → 4.0.0 (commit `9f65781`).
3. Vérifier qu'aucune facture n'est en cours de normalisation (rapport « Factures en attente de
   normalisation »).

## 2. Publier le code sur GitHub

Le paquet livré contient `dgi_compliance_v5.0.5.bundle` (historique Git complet de la branche).
Enregistrer ce fichier dans `Documents\Claude\Projects\ERPNext Updat` ou le laisser dans
`Téléchargements`. Les commandes ci-dessous importent la v5.0.5 dans `feature/HL1-compliance-review`,
avancent `main` sans commit de fusion (fast-forward depuis la v5.0.1), vérifient la version puis
poussent les deux branches. Elles s'arrêtent si le dépôt local contient des modifications non validées.

**Option A — PowerShell (une seule commande)** :

```powershell
& { $ErrorActionPreference='Stop'; $r="$env:USERPROFILE\Documents\GitHub\dgi_compliance"; $b=@("$env:USERPROFILE\Documents\Claude\Projects\ERPNext Updat\dgi_compliance_v5.0.5.bundle","$env:USERPROFILE\Downloads\dgi_compliance_v5.0.5.bundle") | Where-Object { Test-Path $_ } | Select-Object -First 1; if (-not $b) { throw 'dgi_compliance_v5.0.5.bundle introuvable' }; Set-Location $r; if (git status --porcelain) { throw 'Modifications locales non validees' }; function g { git @args; if ($LASTEXITCODE) { throw "git $args : echec" } }; g checkout -q main; g fetch -q $b '+feature/HL1-compliance-review:feature/HL1-compliance-review'; g merge --ff-only -q feature/HL1-compliance-review; if (-not (Select-String -Quiet -Path 'dgi_compliance\__init__.py' -Pattern '5.0.5')) { throw 'Version 5.0.5 absente' }; g log --oneline -3; g push origin main feature/HL1-compliance-review }
```

**Option B — terminal Ubuntu (WSL)** :

```bash
bash <<'EOF2'
set -euo pipefail
REPO="/mnt/c/Users/HP/Documents/GitHub/dgi_compliance"
BUNDLE=""
for B in "/mnt/c/Users/HP/Documents/Claude/Projects/ERPNext Updat/dgi_compliance_v5.0.5.bundle" \
         "/mnt/c/Users/HP/Downloads/dgi_compliance_v5.0.5.bundle"; do [ -f "$B" ] && { BUNDLE="$B"; break; }; done
[ -n "$BUNDLE" ] || { echo "ERREUR: dgi_compliance_v5.0.5.bundle introuvable"; exit 1; }
echo "Bundle: $BUNDLE"
git config --global --add safe.directory "$REPO" 2>/dev/null || true
cd "$REPO"; git config core.fileMode false
[ -z "$(git status --porcelain)" ] || { echo "ERREUR: modifications locales non validees"; git status; exit 1; }
git bundle verify "$BUNDLE" >/dev/null
git checkout -q main
git fetch -q "$BUNDLE" "+feature/HL1-compliance-review:feature/HL1-compliance-review"
git merge --ff-only -q feature/HL1-compliance-review
grep -q '__version__ = "5.0.5"' dgi_compliance/__init__.py && echo "Version 5.0.5 OK"
git log --oneline -3
git push origin main feature/HL1-compliance-review
EOF2
```

**Option C — GitHub Desktop** : après l'option A ou B sans la dernière commande (`push`), le
bouton *Push origin* de GitHub Desktop pousse `main`.

Étiquette facultative (Frappe Cloud déploie à partir de la branche) :
`git tag -a v5.0.5 -m "dgi_compliance v5.0.5 (HL1)" && git push origin v5.0.5`

## 3. Déployer sur Frappe Cloud

1. **Bench Group** → *Apps* → dgi_compliance : vérifier la branche suivie
   (`feature/HL1-compliance-review` pour un bench de test, `main` pour la production).
2. **Deploy** → sélectionner le dernier commit dgi_compliance → *Deploy* (build des assets compris).
3. À la fin du build : **Update** le site concerné. La mise à jour exécute automatiquement :
   - la synchronisation des DocTypes (nouveau DocType *DGI Rapport Fiscal*) ;
   - le patch `dgi_compliance.patches.v5_0.hl1_migrate` ;
   - la synchronisation des champs personnalisés (fixtures) ;
   - les nouveaux formats d'impression.

Équivalent sur un bench auto-hébergé :

```bash
cd ~/frappe-bench
bench get-app --branch feature/HL1-compliance-review https://github.com/bbagabo/dgi_compliance   # ou: cd apps/dgi_compliance && git pull
bench --site <site> migrate
bench build --app dgi_compliance
bench --site <site> clear-cache && bench --site <site> clear-website-cache
bench restart
```

## 4. Paramétrage après la mise à jour (obligatoire)

| Où | Quoi |
|---|---|
| **Company** | Renseigner **RCCM**, **Id. Nat.**, **Tax ID (NIF)**, **Phone No**, **Email**, logo ; adresse de la société cochée *Is Your Company Address*. |
| **DGI Compliance Settings** | Vérifier l'**ISF réel** (le gabarit « AAA-BB-NN » doit être remplacé), NIF, jeton. |
| | Section **Contrôles e-MCF** : Action = *Bloquer* (obligatoire en production), Tolérance = 1 CDF, Casse = *minuscules*, Point de vente par défaut. Il n'y a plus de contrôle de taux de change (v5.0.5). |
| | **Tolérance écart total ERPNext / e-MCF** : passer de 500 à **5 CDF** (500 était la valeur v4). |
| | Bouton **DGI > Synchroniser TOUT** : charge les points de vente (code magasin, adresse, NID) et les taux des groupes. Le taux USD publié par l'e-MCF est seulement informatif. |
| | **Tax Group Mapping** : ajouter les groupes Q (8 %), R, S si utilisés. **Payment Mode Mapping** : chaque mode de paiement utilisé doit être mappé. |
| **Item** | Champs *Taxe spécifique DGI* (ex. `230` ou `10%`) et *Hors champ TVA (DGI)* si nécessaire. |
| **Customer** | *Type de client DGI* (`dgi_customer_type`, liste synchronisée depuis l'e-MCF) et NIF (PM, PC, PL : NIF obligatoire). L'ancien champ *DGI Client Type* (`dgi_client_type`, v1) est supprimé par la mise à jour ; sa valeur est reprise si le nouveau champ est vide. |
| **Nouveau client avec adresse** | Le *Pays* saisi dans la fenêtre de création rapide est conservé, même après « Modifier le formulaire complet » (v5.0.4). Sans pays saisi, le pays par défaut du site (*Paramètres système*) est utilisé et un message demande de vérifier l'adresse. |
| **Sales Invoice** | Nouveau champ **Mode de paiement** (section DGI) : transmis à la DGI et imprimé ; sinon paiements POS / échéancier / défaut. |
| | **Taux de change** (facture en USD) : c'est le taux saisi sur la facture (*Exchange Rate*) qui est transmis à la DGI (`curRate`, prix en CDF, paiements) et imprimé. Le vérifier avant la soumission : il ne peut plus être corrigé après la normalisation. |

Le format **DGI Facture Normalisee** devient le format par défaut des factures de vente.
Les rapports sont accessibles par la recherche : **DGI Rapport Fiscal** → *Générer*.

## 5. Contrôles après déploiement (site de test, environnement e-MCF de test)

1. **DGI > Tester la connexion DGI** dans DGI Compliance Settings.
2. Facture FV en CDF → *Normaliser (DGI)* → soumettre → imprimer → vérifier sur
   `https://developper.dgirdc.cd/sygdef/verification`.
3. Facture en USD (taux saisi, ex. 2 830,75) :
   - l'équivalent CDF apparaît sous le Net à payer, avec « Taux de change de la facture :
     1 USD = 2 830.75 CDF » ;
   - le montant en lettres est en francs congolais ;
   - le même taux est visible sur le portail DGI.
4. Avoir RAN sur la facture du point 2.
5. Rapports X quotidien, Z et A : le bouton *Imprimer* utilise directement le format « DGI Rapport
   Fiscal » (mise en page du rapport e-UF de la DGI, en-tête de la société), sans données JSON.
6. Dérouler la campagne `TESTS_v5.0.5.md` (cas DGI 1 à 29, R01 à R03) en mode HT et TTC.

## 6. Tests automatisés (bench de test uniquement)

```bash
bench --site <site_de_test> set-config allow_tests true
bench --site <site_de_test> run-tests --module dgi_compliance.tests.test_calc_words
bench --site <site_de_test> run-tests --module dgi_compliance.tests.test_hl1_emcf
```
Les tests d'intégration créent des données (société de test « DGI Test SARL » requise, variable
`DGI_TEST_COMPANY` sinon) et simulent l'e-MCF : **ne jamais les lancer sur la production**.

## 7. Retour arrière

1. Frappe Cloud → Bench Group → *Deploys* → redéployer le commit `9f65781` (tag `v4.0.0`) ou
   remettre la branche suivie sur `main` v4, puis *Update* du site.
2. Les champs ajoutés en v5 restent présents mais sans effet ; le format « DGI Sales Invoice » v4
   est toujours installé : *Customize Form* → Sales Invoice → *Default Print Format*.
3. En cas de problème de données : restaurer la sauvegarde du point 1.
4. Local : `git checkout main && git reset --hard backup/pre-v4.0.0` n'est **pas** nécessaire —
   la v4 reste le commit `9f65781` sur `main` tant que la v5 n'y est pas fusionnée.
