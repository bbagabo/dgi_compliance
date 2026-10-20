# DGI Compliance v5.0.4 — Notes de version

Base : v5.0.3. Correctif sur la création d'un client avec adresse.

## Problème

Erreur à l'enregistrement d'un nouveau client : « Valeurs requises manquantes – Les champs suivants
sont obligatoires pour créer une adresse : Country ».

Ce n'est pas un paramètre : la règle est écrite dans le code d'ERPNext.

- **Contrôle :** `erpnext/selling/doctype/customer/customer.py`, fonction `make_address`. Quand un
  nouveau client contient une ligne d'adresse, ERPNext crée l'adresse principale et exige la ville
  et le pays.
- **Cause :** la fenêtre de création rapide garde le *Pays* dans un champ intermédiaire
  (`country_address`). Elle ne le transmet sous le nom `country` que si le client est enregistré
  depuis la fenêtre elle-même (`erpnext/public/js/utils/contact_address_quick_entry.js`). Avec
  « Modifier le formulaire complet », le pays saisi n'est pas transmis. Le formulaire complet n'a
  pas de champ Pays pour le corriger, donc l'enregistrement échoue.

## Correctif (sans modifier ERPNext)

L'application ajoute un événement *before_insert* sur le client
(`dgi_compliance.edef.customer.map_quick_entry_address_country`) :
- le pays saisi dans la fenêtre rapide est repris ;
- si aucun pays n'a été saisi, le pays par défaut du site (*Paramètres système*, sinon celui de
  la société par défaut) est utilisé. Un message orange demande alors de vérifier l'adresse, car
  son pays détermine la Matrice C (factures FV ou à l'exportation).

## Tests

- 63 tests d'intégration (+3 : W01 à W03), 12 tests unitaires : tous réussis.
- Scénario reproduit avant correctif (erreur « Country »), puis corrigé.
- Installation neuve et mise à jour depuis une sauvegarde v4 rejouées.
