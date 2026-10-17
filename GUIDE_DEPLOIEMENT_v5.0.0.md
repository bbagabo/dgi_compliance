# Guide de déploiement — DGI Compliance v5.0.0 (HL1)

Architecture : Frappe Cloud, application installée depuis le dépôt GitHub `bbagabo/dgi_compliance`.
Aucun fichier de Frappe ou d'ERPNext n'est modifié : la mise à jour se fait par un simple
déploiement de l'application puis la migration du site.

## 1. Avant la mise à jour

1. Frappe Cloud → **Site** → *Backups* → **Schedule Backup** (cocher *Include files*).
2. Noter la version actuelle : *Apps* → dgi_compliance → 4.0.0 (commit `9f65781`).
3. Vérifier qu'aucune facture n'est en cours de normalisation (rapport « Factures en attente de
   normalisation »).

## 2. Publier le code sur GitHub (terminal Ubuntu WSL)

Le paquet livré contient `dgi_compliance_v5.0.0.bundle` (historique Git complet de la branche).
Bloc unique à coller dans le terminal Ubuntu (WSL) : il importe la branche
`feature/HL1-compliance-review` depuis le bundle, vérifie la version puis la pousse sur GitHub.

```bash
bash <<'EOF2'
set -euo pipefail
REPO="/mnt/c/Users/HP/Documents/GitHub/dgi_compliance"
BUNDLE="/mnt/c/Users/HP/Documents/Claude/Projects/ERPNext Updat/dgi_compliance_v5.0.0.bundle"
git config --global --add safe.directory "$REPO" 2>/dev/null || true
cd "$REPO"; git config core.fileMode false
[ -z "$(git status --porcelain)" ] || { echo "ERREUR: modifications locales non validees"; git status; exit 1; }
git bundle verify "$BUNDLE" >/dev/null
git checkout -q main
git fetch -q "$BUNDLE" "+feature/HL1-compliance-review:feature/HL1-compliance-review"
git checkout -q feature/HL1-compliance-review
grep -q '__version__ = "5.0.0"' dgi_compliance/__init__.py && echo "Version 5.0.0 OK"
git log --oneline -8
git push -u origin feature/HL1-compliance-review
EOF2
```

Mise en production (après validation sur le site de test) :

```bash
cd /mnt/c/Users/HP/Documents/GitHub/dgi_compliance && git checkout main && git merge --ff-only feature/HL1-compliance-review \
 && git tag -a v5.0.0 -m "dgi_compliance v5.0.0 (HL1)" && git push origin main v5.0.0
```

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
| | Section **Contrôles e-MCF (v5.0.0)** : Action = *Bloquer*, Tolérance = 1 CDF, Contrôle du taux = *Bloquer* (1 %), Casse = *minuscules*, Point de vente par défaut. |
| | **Tolérance écart total ERPNext / e-MCF** : passer de 500 à **5 CDF** (500 était la valeur v4). |
| | Bouton **DGI > Synchroniser TOUT** : charge les points de vente (code magasin, adresse, NID), les taux des groupes et le taux USD de référence. |
| | **Tax Group Mapping** : ajouter les groupes Q (8 %), R, S si utilisés. **Payment Mode Mapping** : chaque mode de paiement utilisé doit être mappé. |
| **Item** | Champs *Taxe spécifique DGI* (ex. `230` ou `10%`) et *Hors champ TVA (DGI)* si nécessaire. |
| **Customer** | *Type de client DGI* et NIF (PM, PC, PL : NIF obligatoire). |
| **Sales Invoice** | Nouveau champ **Mode de paiement** (section DGI) : transmis à la DGI et imprimé ; sinon paiements POS / échéancier / défaut. |

Le format **DGI Facture Normalisee** devient le format par défaut des factures de vente.
Les rapports sont accessibles par la recherche : **DGI Rapport Fiscal** → *Générer*.

## 5. Contrôles après déploiement (site de test, environnement e-MCF de test)

1. **DGI > Tester la connexion DGI** dans DGI Compliance Settings.
2. Facture FV en CDF → *Normaliser (DGI)* → soumettre → imprimer → vérifier sur
   `https://developper.dgirdc.cd/sygdef/verification`.
3. Facture en USD → équivalent CDF sous le Net à payer, montant en lettres en francs congolais.
4. Avoir RAN sur la facture du point 2.
5. Rapports X quotidien, Z et A.
6. Dérouler la campagne `TESTS_v5.0.0.md` (cas DGI 1 à 29, R01 à R03) en mode HT et TTC.

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
