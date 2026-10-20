# DGI Compliance v5.0.3 — Notes de version

Base : v5.0.2. Deux ajustements demandés avant le démarrage de l'homologation.

## 1. Un seul type de client DGI : `dgi_customer_type`

- L'ancien champ *DGI Client Type* (`dgi_client_type`) est supprimé. Il avait été créé sur le
  client par la v1.0 (liste fixe PP / PM / ETR). Le nettoyage de la v3.0 ne l'avait retiré que
  de la facture.
- Le seul champ conservé est *Type de client DGI* (`dgi_customer_type`). C'est un lien vers la
  liste *DGI Customer Type*, synchronisée depuis l'e-MCF (types PP, PM, PC, PL, AO).
- Avant la suppression, la valeur de l'ancien champ est reprise dans `dgi_customer_type` quand
  celui-ci est vide et que la valeur existe dans la liste DGI. « ETR » n'existe pas dans la liste
  DGI et n'est donc pas reprise.
- La colonne de l'ancien champ reste en base de données : aucune donnée n'est effacée, le champ
  disparaît seulement des écrans.

## 2. Matrice C : type de facture / pays de l'adresse du client

- La matrice s'appelle désormais « C - Invoice Type / Pays (adresse client) ». Les lignes
  existantes sont renommées par la mise à jour, avec leurs réglages.
- Le pays est toujours lu dans l'adresse du client, dans cet ordre :
  1. l'adresse de facturation choisie sur la facture ;
  2. sinon l'adresse principale du client ;
  3. sinon une autre adresse liée au client.
- **Correction :** sans adresse sur la facture, la v5.0.2 prenait le pays de la société (toujours
  la RDC). Une facture à l'exportation pour un client étranger était alors bloquée, et une facture
  FV pour ce client était acceptée. Le pays de la société n'est plus utilisé.
- Un client sans aucune adresse est considéré comme local (CD), comme avant.
- En cas de blocage, le message indique le pays lu et l'adresse utilisée, par exemple :
  « pays de l'adresse client : France (Non-CD), adresse principale du client … ».
- La colonne de la matrice s'appelle « Pays de l'adresse client » (CD = pays local défini dans
  DGI Compliance Settings, Non-CD = tout autre pays).

## Tests

- 60 tests d'intégration (+7 : V01 à V07), 12 tests unitaires : tous réussis.
- Installation neuve et mise à jour depuis une sauvegarde v4 rejouées.
- Mise à jour : le patch `v5_0.v503_customer_type_matrix_c` s'exécute automatiquement lors de
  l'*Update* du site.
