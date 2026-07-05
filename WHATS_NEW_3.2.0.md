# DGI Compliance 3.2.0 - Notes de version

Version mineure : matrice de devises configurable, equivalence CDF a l'impression, et durcissement
de securite (audit livre separement).

## 1. Matrice de devises (autorisation + conversion par type de facture)

Nouveau DocType enfant **DGI Currency Rule** (table dans DGI Compliance Settings > Devises) :
- `Devise` x `Type de facture` (Any/FV/EV/FT/ET/FA/EA) -> `Autorisee` (oui/non) ;
- `Source du taux` par devise : **ERPNext** (conversion_rate de la facture), **DGI Officiel**
  (taux publie via currencyRates), ou **Manuel** (valeur saisie).
- Reglage global `Application matrice devises` : **Enforce** (bloque), **Warn only**, **Off**.
  Si la table est vide, aucune restriction (compatibilite ascendante).

Validation (`validate_currency`) : une facture dont la devise n'est pas autorisee pour son type
est bloquee (ou avertie). La regle la plus specifique l'emporte (type exact > Any). Une devise
absente de la matrice non vide est refusee. L'override admin reste possible.

Le mapper choisit le taux (`curRate`) selon la `Source du taux` de la regle correspondante.
Valeurs semees par defaut : CDF (Any, autorisee) et USD (Any, autorisee, taux ERPNext).

## 2. Devises etrangeres : montants en CDF + equivalence obligatoire a l'impression

Confirme : la DGI interprete tout montant comme du CDF. L'app envoie donc en CDF (champs `base_*`).
Exemple : article 100 USD, taux 2 300 -> prix unitaire envoye = **230 000 CDF** ; 2 articles ->
montants en CDF, plus `curCode`=USD, `curRate`=2300, `curDate`.

Le format d'impression **DGI Sales Invoice** affiche desormais, pour toute facture en devise
etrangere, un bloc obligatoire **"Equivalence en Francs Congolais (CDF)"** : Total HT, Total TVA,
Total TTC en CDF + le taux applique (les valeurs reellement transmises a la DGI).

## 3. Durcissement de securite (voir rapport d'audit joint)

- **Masquage des secrets** dans DGI Exchange Log : toute valeur dont la cle evoque un secret
  (token, authorization, password, jwt, bearer...) est remplacee par `***REDACTED***` avant
  enregistrement.
- **Controle de permission par document** sur les endpoints normalize/retry (en plus du role).
- **Avertissement HTTPS** : DGI Compliance Settings alerte si l'URL e-DEF active n'est pas en HTTPS.

Rapport complet : `Audit_Securite_DGI_Compliance_v3.2.0_FR.docx` (constats par severite +
feuille de route de renforcement).

## Mise a niveau

```bash
bench --site <site> migrate        # joue v3_2.seed_currency_rules
bench --site <site> clear-cache && bench build --app dgi_compliance
```
Verifiez ensuite : DGI Compliance Settings > Devises (CDF/USD semes) et le mode d'application.
