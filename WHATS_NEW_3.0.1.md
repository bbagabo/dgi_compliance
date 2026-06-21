# DGI Compliance 3.0.1 - Notes de version

Correctif de la 3.0.0. Deux changements, valides contre le protocole officiel e-DEF
(OpenAPI : https://developper.dgirdc.cd/edef/swagger/v1/swagger.json).

## 1. Correctif bloquant - journal d'echange (cause de "les champs DGI ne se remplissent pas")

Le champ **Direction** du DocType **DGI Exchange Log** etait un `Select` a liste fermee
(`validate, create, confirm, cancel, reconcile, exchange-diff, retry, token-check, sync`).
Or le code v3.0 journalise aussi les actions **`normalize-manual`** (bouton "Normaliser (DGI)")
et **`matrix-validate`**. Resultat : l'insertion du log echouait avec
"Direction ne peut pas etre 'normalize-manual'...", ce qui interrompait la normalisation et
laissait les champs DGI vides.

Correctif (cause racine) : `Direction` passe en champ **Data** (texte libre). Il accepte
desormais n'importe quelle action, actuelle ou future, et ne peut plus bloquer un echange.
`log_exchange` tronque par securite a 60 caracteres et n'echoue jamais.

> Aucune migration manuelle : le changement de type de champ s'applique au `bench migrate`.
> Les valeurs existantes du journal sont conservees et restent filtrables.

## 2. Alignement protocole - envoi de `client.type`

Le contrat officiel **ClientDto** exige un champ **`type`** (ClientTypeEnum : PP/PM/PC/PL/AO)
et un `typeDesc`. La 3.0.0 ne les envoyait pas. La 3.0.1 renseigne **`client.type`** (et
`typeDesc`) dans la requete de normalisation, resolu via le mapping Type de client v3.0
(champ explicite -> fiche client -> defaut du mapping natif, Matrice G). La valeur est
validee (PP/PM/PC/PL/AO) avant envoi.

Le reste du contrat est deja conforme : points d'API (`/api/info/*`, `/api/invoice`,
`/api/invoice/{uid}/confirm|cancel`), corps de confirmation (`total`, `vtotal`) et champs de
reponse (`uid`, `total`, `vtotal`, `codeDEFDGI`, `nim`, `counters`, `dateTime`, `qrCode`,
`errorCode`, `errorDesc`).

## Mise a niveau

```bash
bench --site <site> migrate
bench --site <site> clear-cache && bench build --app dgi_compliance
```
