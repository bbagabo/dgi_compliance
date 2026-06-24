# DGI Compliance 3.1.1 - Notes de version

Deux ajouts demandes, sans impact sur le core ERPNext.

## 1. Format d'impression DGI defini par defaut (automatique)

`DGI Sales Invoice` devient le format d'impression par defaut des Sales Invoice, via un
**Property Setter** (propriete `default_print_format` au niveau du DocType).

- Mecanisme standard et **upgrade-safe** de Frappe : il ne modifie AUCUN fichier core, ni la
  comptabilite, ni le stock, ni les validations. Il change uniquement le format pre-selectionne
  a l'impression.
- **Reversible** : supprimez le Property Setter (Sales Invoice / default_print_format) pour
  revenir a votre format precedent.
- Applique automatiquement a l'installation (after_install) et a la mise a jour
  (patch `v3_1.set_default_print_format`, idempotent), apres synchronisation du format.

> Rappel : pour MODIFIER la mise en page, dupliquez le format (il est Standard, donc non editable
> en l'etat sur Frappe Cloud) - voir la procedure dans la reponse precedente.

## 2. Factures en devise etrangere (USD) -> valeurs en CDF + taux

Confirmation et fiabilisation : la DGI travaille en monnaie locale (CDF). L'application envoie
donc **tous les montants en CDF** (champs ERPNext `base_*`) et joint le **taux de change** pour
que la DGI puisse afficher la valeur en devise.

Comment ERPNext gere le multi-devise (verifie) : pour une facture en USD avec societe en CDF,
ERPNext stocke `grand_total` en USD, `base_grand_total` en CDF, et `conversion_rate` = CDF pour
1 USD. L'app exploite exactement ces champs.

Ce qui est envoye a l'API e-DEF (conforme a l'OpenAPI InvoiceRequestDataDto / PaymentDto) :

- montants des lignes et paiements : en **CDF** (base_*), toujours positifs ;
- `curCode` : devise de la facture (ex. USD) ;
- `curRate` : **CDF par 1 unite de devise** (= conversion_rate ERPNext). La DGI obtient la valeur
  devise = total CDF / curRate ;
- `curDate` : date/heure du document ;
- les memes `curCode`/`curRate` sont desormais ajoutes a chaque ligne de paiement.

Fiabilisation du taux (`curRate`) avec repli en cascade :
1. `conversion_rate` de la facture (le taux reellement utilise) ;
2. sinon recalcul `base_grand_total / grand_total` ;
3. sinon le taux officiel publie par la DGI (DGI Reference Value `Currency Rate::<CCY>`).

Une facture deja en CDF n'emet aucun bloc devise (curCode/curRate absents), comme attendu.

## Mise a niveau

```bash
bench --site <site> migrate          # joue v3_1.set_default_print_format
bench --site <site> clear-cache && bench build --app dgi_compliance
```
