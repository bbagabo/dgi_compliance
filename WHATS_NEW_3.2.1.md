# DGI Compliance 3.2.1 - Notes de version

Correctif d'impression + comportement des avoirs sur le formulaire.

## 1. Erreur d'impression corrigee

Message : "Error in print format on line 127: 'dict object' has no attribute 'base_grand_total'".
Cause : en 3.2.0, la cle des totaux `base_grand_total` a ete renommee `grand_total_cdf`, mais
l'ancienne ligne "Total CDF (indicatif)" du tableau des totaux la referencait encore.
Correctif : ligne redondante supprimee (l'equivalence CDF est deja affichee dans le bloc dedie
"Equivalence en Francs Congolais"). L'impression fonctionne pour les factures CDF et devises.

## 2. Type de facture DGI et "Nature de l'avoir" selon is_return

- **Liste du Type de facture DGI restreinte au contexte** :
  - facture normale (is_return decoche) -> seuls **FV, FT, EV, ET** sont selectionnables ;
  - retour / note de credit (is_return coche) -> seuls **FA, EA** sont selectionnables.
- **"Nature de l'avoir"** (et la reference d'origine + sa description) :
  - **visible uniquement** pour un retour (ou un type explicite FA/EA) ;
  - **masquee** dans tous les autres cas.
  Applique a la fois cote serveur (depends_on, robuste) et cote client (toggle immediat).
- A l'activation de is_return, le type passe automatiquement a FA (ou EA si export) ; a la
  desactivation, un type FA/EA est efface pour laisser la deduction FV/EV reprendre.

## 3. Document de reference (joint)

`Mapping_Champs_Sales_Invoice_DGI_v3.2.1_FR.docx` : tableau de correspondance des champs de la
facture (custom DGI + natifs) avec le payload e-DEF et les DocTypes lies, pour guider les
evolutions futures.

## Mise a niveau

```bash
bench --site <site> migrate
bench --site <site> clear-cache && bench build --app dgi_compliance
```
> Important : `bench build` + `clear-cache` rechargent le format d'impression et les scripts client
> (sinon l'ancien format en cache peut persister).
