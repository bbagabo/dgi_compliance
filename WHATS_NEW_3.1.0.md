# DGI Compliance 3.1.0 - Notes de version

Version mineure : nouveau format d'impression normalise + 3 correctifs cibles.
Mise a niveau idempotente (`bench migrate`).

## 1. Format d'impression DGI normalise (+ QR) et erreur "DGI eMCF POS"

**Erreur corrigee** : "Sales Invoice: les options doivent etre un type de document valide pour le
champ 'DGI eMCF POS' (ligne 72)". Cause : un champ personnalise Link/Table pointe vers un DocType
inexistant, ce qui invalide tout le formulaire Sales Invoice (et empeche d'enregistrer le format
d'impression par defaut via Customize Form).
- Patch `v3_1.fix_invalid_link_fields` : detecte tout Custom Field Link/Table dont les `options`
  ne sont pas un DocType valide ; repointe vers `DGI EMCF` si le champ vise l'e-MCF/POS, sinon
  supprime le champ casse. Actions tracees dans Error Log. Apres migration, le format par defaut
  s'enregistre normalement.

**Nouveau format d'impression "DGI Sales Invoice"** (Jinja), calque sur le modele normalise
(Business Central) :
- En-tete vendeur (NIF, Reference ISF), bloc client, informations generales ;
- Tableau des lignes : Type, Code, Description, Qte, UM, PU HT, Montant HT, Remise, Groupe
  taxation, TVA %, Net HT ;
- Totaux (HT, Remise, TVA, TTC + CDF indicatif), montant en toutes lettres ;
- "Montant TVA - Specification" par groupe A-P ;
- Bloc securite DEF : Code DEF/DGI, DEF NID, DEF Compteurs, DEF Heure, NIM, UID + **QR code**.
- Helpers Jinja (hook `jinja`) : `dgi_invoice_lines`, `dgi_totals`, `dgi_tax_summary`,
  `dgi_isf`, `dgi_pos_nid`, `dgi_amount_in_words`, `dgi_item_type`, `dgi_item_tax_group`.
  Tous renvoient des montants positifs (les avoirs sont negatifs cote ERPNext).

## 2. Retours / notes de credit : FA / EA imposes

**Erreur corrigee** : "Un retour / avoir ERPNext doit etre de type FA ou EA (type actuel: FV)".
- `dgi_invoice_type` est marque **no_copy** : un retour n'herite plus du FV de la facture d'origine.
- Nouveau hook `enforce_return_invoice_type` (s'execute en PREMIER a la validation) : si la facture
  est un retour (is_return), le type DGI est **force** a FA (ou EA si export). FV devient impossible
  sur un retour, et l'utilisateur n'est jamais bloque.
- Cote client : le champ "Type de facture DGI" est **restreint a FA/EA** sur un retour et
  auto-renseigne. La regle de coherence existante reste comme filet de securite.

## 3. Normalisation des retours : valeurs absolues dans le payload

**Erreur corrigee** : "items[0].quantity doit etre > 0". Les avoirs utilisent des quantites
negatives dans ERPNext (logique comptable), refusees par la DGI.
- Le payload e-DEF envoie desormais des **valeurs absolues** : quantites et prix des lignes,
  montants de paiement. La logique interne ERPNext reste inchangee (negatif).
- La reconciliation des totaux et de la TVA (ERPNext vs DGI) compare egalement en valeur absolue,
  evitant les faux "ecart total > tolerance" sur les avoirs.

## 4. Coherence ERPNext <-> DGI

- Types de facture: FV/EV standard, FT/ET acompte (explicite), FA/EA avoirs (auto + imposes).
- `client.type` (PP/PM/...) deja envoye depuis 3.0.1 ; ISF source unique (Settings) depuis 3.0.
- Endpoints, confirm (total/vtotal), reponses (uid/codeDEFDGI/nim/qrCode...) conformes a l'OpenAPI.

## Mise a niveau

```bash
bench --site <site> migrate        # joue v3_1.fix_invalid_link_fields
bench --site <site> clear-cache && bench build --app dgi_compliance
```
Apres migration : ouvrez une facture > menu Imprimer > format **DGI Sales Invoice** ; vous pouvez
le definir par defaut (Customize Form > Sales Invoice > Default Print Format).
