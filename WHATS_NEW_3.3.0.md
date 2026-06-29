# DGI Compliance 3.3.0 - Notes de version

Champ d'enregistrement client, remises ligne, rabais FA/EA, compteur d'impression, libelles
d'en-tete par type, et corrections du format d'impression.

## 1. Champ d'enregistrement client (RCCM / Id. Nat.)

- Nouveau champ **Customer.dgi_registration_no** : "N d'enregistrement (RCCM / Id. Nat.)".
- Utilise par la **Matrice B** (champ obligatoire req_registration_no) ; repli sur le NIF si vide.
- Affiche sur la fiche client et sur la facture normalisee (bloc client). Inclus automatiquement
  dans les exports clients (Custom Field standard).

## 2. Remises au niveau ligne

Pourquoi elles "n'etaient pas prises en compte" : le prix NET (apres remise) etait envoye a la DGI,
donc la remise etait "absorbee" dans le prix et n'apparaissait pas distinctement ; de plus
l'impression affichait Montant HT = Net HT.

Corrections (ERPNext autorise les remises ligne, l'app ne les bloque pas) :
- L'app transmet desormais **originalPrice** (prix brut avant remise) dans le payload e-DEF quand
  une remise ligne existe ; le prix net continue de piloter le total (reconciliation inchangee).
  Reglage **"Transmettre les remises ligne a la DGI"** (DGI Settings, defaut active).
- Le format d'impression calcule correctement : **Montant HT (brut)**, **Remise** = brut - net,
  **Net HT** par ligne.

## 3. Publication des rabais (RRR -> FA / EA)

- Renseigner une **"Nature de l'avoir"** (COR/RAN/RAM/**RRR** rabais) impose un type **FA ou EA** :
  la validation bloque tout autre type. Combine au verrouillage des retours (FA/EA auto), un rabais
  est ainsi systematiquement lie a une facture d'avoir et trace par son code (ex. RRR).

## 4. Compteur d'impression et etiquette Original / Duplicata

- Nouveaux champs : **Compteur d'impressions DGI**, **Premiere impression**, **Derniere impression**
  (metadonnees conservees sur la facture).
- A chaque impression (hook before_print, factures soumises), le compteur s'incremente et les
  horodatages sont mis a jour.
- Le PDF affiche **ORIGINAL** a la premiere impression, **DUPLICATA** ensuite.
  > Remarque : un apercu d'impression peut aussi incrementer le compteur (comportement Frappe).

## 5. Libelles d'en-tete selon le type DGI

L'en-tete imprime reflete le type enregistre :
FV -> Facture de vente | EV -> Facture de vente a l'export | FT -> Facture d'acompte |
ET -> Facture d'acompte a l'export | FA -> Facture d'avoir | EA -> Facture d'avoir a l'export.

## 6. Sous-total avec remise + corrections d'impression

- Bloc des totaux restructure : **Montant facture (HT brut)**, **Remise**, **TVA**,
  **Net a payer (TTC)** - conforme a votre modele BI. L'equivalence CDF suit la meme structure.
- Corrections du format (vues sur ACC-SINV-2026-00008) :
  - "DEF Compteurs" n'affiche plus le type en double (ex. "5/8 FV", plus "5/8 FV FV") ;
  - la "Specification TVA" est exprimee dans la **devise de la facture** (plus de montants CDF
    affiches avec le symbole $).

## Mise a niveau

```bash
bench --site <site> migrate
bench --site <site> clear-cache && bench build --app dgi_compliance
```
> `clear-cache` + `build` rechargent le format d'impression et les scripts client.
