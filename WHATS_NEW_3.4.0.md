# DGI Compliance v3.4.0 — Notes de version

Basée sur v3.3.0. Alignée sur le protocole officiel **e-MCF-SFE v1.3** (Spécifications techniques des dispositifs électroniques fiscaux, DGI RDC).

## 1. Tax Group Mapping — refonte du comportement

Le mapping vers les groupes de taxation e-DEF (A–P) se fait désormais à deux niveaux :

| Niveau | Champ ERPNext | Priorité |
|---|---|---|
| Ligne | Modèle de taxe d'article (Item Tax Template) | 1 (gagne toujours) |
| Facture | Modèle de Taxes et Frais de Vente (Sales Taxes and Charges Template, champ `taxes_and_charges`) | 2 |

- Nouvelle colonne **Modèle Taxes et Frais de Vente** dans la table *Tax Group Mapping* (DGI Compliance Settings). La colonne *Item Tax Template* existante est conservée.
- L'équivalent mappé est transmis dans le payload e-MCF (`ItemDto.taxGroup`) — résolution automatique par ERPNext.
- **Le Default Tax Group n'est utilisé QUE lorsqu'aucune taxe n'est spécifiée** sur la facture (ni modèle facture, ni modèle ligne, ni ligne de taxes). Il ne remplace jamais un mapping.
- Une taxe **spécifiée mais non mappée bloque la normalisation** avec un message explicite indiquant le modèle à mapper (nouvelle erreur `TaxGroupMappingError`, statut DGI = Error + journalisation). Le groupe par défaut n'est plus envoyé silencieusement à la place.
- Les matrices de validation et le format d'impression utilisent la même résolution (non bloquante de leur côté).

Code : `DGIComplianceSettings.tax_group_for_invoice(doc, item, strict)` ; `tax_group_for()` conservé pour compatibilité.

## 2. Commentaires normalisés A–H

- 8 nouveaux champs sur la facture de vente : **Commentaire A … Commentaire H** (`dgi_comment_a` … `dgi_comment_h`), section repliable « DGI - Commentaires normalisés (A-H) ».
- Transmis dans le payload e-MCF sous `cmta` … `cmth` (InvoiceRequestDataDto, protocole v1.3). Les champs vides ne sont pas envoyés.
- Affichés dans un bloc « Commentaires » du format d'impression *DGI Sales Invoice* (uniquement les lignes renseignées).
- La modification d'un commentaire après normalisation repasse le brouillon en *Pending* (re-normalisation requise), comme pour les montants.

## 3. Remises — payload conforme e-MCF v1.3

Dans `ItemDto`, lorsqu'une ligne porte une remise :

- `price` = **prix net APRÈS remise** (conforme ERPNext, `base_rate`/`base_net_rate`) — inchangé, pilote le total DGI ;
- `originalPrice` = **prix AVANT remise** (valeur absolue, `base_price_list_rate`) ;
- `priceModification` = description de la modification, ex. `Remise 10%` (nouveau, champ prévu par le protocole).

La DGI reçoit donc les deux valeurs (prix réel sans remise + prix net). Contrôle ajouté : `originalPrice >= price` sinon payload rejeté avant envoi. Avoirs : valeurs positives (abs), comme en v3.1+.

## 4. Divers

- `app_version` / `pyproject` → 3.4.0 ; fixtures étendues (10 nouveaux Custom Fields).
- Aucun patch de données nécessaire : `bench migrate` synchronise le DocType enfant (nouvelle colonne) et les fixtures.

## Mise à niveau

```bash
bench get-app <repo> --branch <tag-3.4.0>   # ou remplacement du dossier de l'app
bench --site <site> migrate
bench build && bench restart
```

Après migration : ouvrir **DGI Compliance Settings → Tax Group Mapping** et compléter les correspondances *Modèle Taxes et Frais de Vente* → groupe A–P pour tous les modèles utilisés.
