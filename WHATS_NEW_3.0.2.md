# DGI Compliance 3.0.2 - Notes de version

Correctif de la 3.0.1. Cible l'echec "Normalisation DGI: payload invalide - isf doit respecter
le format AAA-BBB-NN".

## Validation ISF assouplie (la DGI fait autorite sur le format)

La 3.0.1 imposait un motif rigide cote client : `[A-Za-z]{3}-[A-Za-z]{3}-\d{2}`. Ce motif
rejetait a tort des ISF reels d'un autre format (ex. segments alphanumeriques) et bloquait la
normalisation avant meme l'envoi a la DGI.

La 3.0.2 :

- bloque uniquement si l'ISF est **vide** (message clair : a renseigner dans DGI Compliance Settings) ;
- bloque le **gabarit d'exemple** `AAA-BBB-NN` (souvent laisse par erreur), avec un message explicite ;
- pour toute autre valeur, **n'impose plus de format** : la requete part a la DGI, qui valide
  l'ISF et renvoie un message d'erreur faisant autorite si besoin ;
- l'ISF est **detoure** (espaces de debut/fin supprimes) a la source.

> Aucune migration : simple mise a jour de code. `bench migrate` + `clear-cache` + build.

## Que faire si l'erreur persiste

1. Ouvrez **DGI Compliance Settings** et verifiez que le champ **ISF** contient votre identifiant
   reel fourni par la DGI (et non le gabarit `AAA-BBB-NN`).
2. Enregistrez, videz le cache (le singleton est mis en cache), puis re-normalisez le brouillon.
3. Si la DGI renvoie ensuite une erreur sur l'ISF, c'est qu'il n'est pas reconnu cote DGI pour
   cet environnement (Test/Production) : utilisez l'ISF correspondant a l'environnement actif.
