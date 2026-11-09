# DGI Compliance 5.4.0 - Deux utilisateurs suffisent, référence séquentielle par point de vente

Branche `feature/HL2-v5.4.0-deux-utilisateurs-ref-pdv`, 9 novembre 2026, à partir de `main` (v5.3.2).

## Pourquoi

1. **Utilisateurs.** Trois actions exigent deux personnes : la validation d'un MCF proposé, l'approbation d'un
   Poste MCF (enrôlement) et la bascule manuelle de canal. Seul un DGI Fiscal Manager pouvait approuver. Quand le
   responsable faisait lui-même l'enrôlement ou la proposition (cas fréquent : c'est lui qui installe la caisse),
   il fallait un **second DGI Fiscal Manager**, donc un troisième utilisateur dans une entreprise « gérant +
   caissière ». Le compte technique de la passerelle, créé automatiquement, n'est pas un utilisateur et ne doit
   jamais approuver.
2. **Référence.** L'entreprise veut garder sa numérotation ERPNext (un seul journal de facturation) et imprimer
   une référence secondaire, continue, propre à chaque point de vente. L'exigence SFE 1.15 / 1.2.1 k (« référence
   unique, série ininterrompue par année de gestion ») n'était que partiellement couverte (écart G21 : un
   brouillon jamais normalisé laisse un trou dans la série ERPNext).

## Hypothèses vérifiées avant de coder

| Hypothèse | Constat (source) | Décision |
|---|---|---|
| Il faut 3 utilisateurs | 2 humains suffisaient si la caissière enrôle et propose ; sinon un 2e DGI Fiscal Manager était exigé. Le « 3e » compte de la passerelle est technique (Website User, sans bureau). | Rôle restreint **DGI Approbateur** : 2 personnes dans les deux sens. |
| Donner « DGI Admin » à la machine / l'utilisateur du MCF | La machine approuverait sa propre clé ; son compte (sur le PC de caisse) deviendrait administrateur fiscal ; l'audit montrerait un robot comme approbateur. | **Refusé.** Un approbateur est toujours une personne ; les comptes techniques sont refusés même s'ils reçoivent un rôle. |
| Chaque MCF / e-MCF doit avoir sa séquence | Elle existe déjà et c'est la DGI qui la produit : « DEF Compteurs » FC/TC par NIM / NID (protocole SFE-MCF v1.0, C1h et 38h ; API e-MCF v1.3, `counters`), déjà stockés et imprimés. | Le SFE ne la duplique pas. |
| La sous-séquence SFE suit le MCF / e-MCF | Un point de vente bascule entre son e-MCF et son MCF (secours) ; l'équipement n'est connu qu'au retour de la DGI. Une série par équipement couperait la série du point de vente. | Série portée par le **point de vente fiscal** (ou par la société, paramètre). L'équipement est tracé ligne par ligne. |
| La référence doit survivre aux échecs et nouvelles tentatives | Un numéro réservé avant l'envoi serait perdu quand la DGI refuse ou qu'un brouillon est supprimé. | Numéro attribué **quand la DGI renvoie les éléments de sécurité** : aucun numéro consommé par un échec, aucun trou. |
| Libellé « Réf. DGI » | Le numéro est attribué par le SFE, pas par la DGI ; les éléments DGI sont le Code DEF/DGI, le NIM/NID et les compteurs DEF. | Libellé par défaut **« Réf. PDV »**, paramétrable. |

## Ce qui change

### Séparation des tâches (`fiscal/approvals.py`)
- Nouveau rôle **DGI Approbateur** (accès bureau) : valide ou refuse un MCF proposé, approuve un Poste MCF,
  approuve (valide) une bascule de canal. Aucun droit de configuration : pas de code d'enrôlement, pas de
  révocation, pas de paramètres, pas de dérogation de canal, pas d'arbitrage de point de vente.
- Règles communes aux trois approbations : approbateur **humain** (System User actif ; jamais un compte
  `@dgi-gateway.invalid`, DGI Gateway ou DGI MCF Agent, même s'il reçoit un rôle de gestionnaire) ;
  **l'initiateur n'approuve jamais** tant que la règle des deux personnes est active ; règle désactivée
  (entreprise d'une seule personne) : l'auto-approbation est acceptée et journalisée **AUTO_APPROBATION** (Alerte).
- Un approbateur ne modifie pas une demande de bascule : il l'approuve telle quelle (Frappe 16 exige le droit
  d'écriture pour valider, les champs de la demande sont donc figés pour lui).
- Les deux cases « deux personnes » des paramètres ne sont modifiables que par un System Manager
  (niveau de permission 1) ; tout changement est journalisé **PARAMETRE_CONTROLE** (Alerte s'il affaiblit la règle).
- DGI Compliance Settings › MCF › **Contrôler la séparation des tâches** : initiateurs, approbateurs, comptes
  techniques exclus, et si deux personnes distinctes existent pour chaque contrôle.

### Référence séquentielle par point de vente (`fiscal/sequence.py`)
- Format `CODE-AAAA-NNNNNN`, par exemple `KIN01-2026-000001`. Numéro de facture ERPNext inchangé (il reste le
  FN / rn transmis à la DGI) ; **rien de nouveau n'est envoyé à la DGI**.
- Code : champ **Code de séquence (Réf. PDV)** du point de vente fiscal (2 à 10 majuscules ou chiffres,
  unique, proposé depuis le code magasin, figé dès la première référence). Factures sans point de vente :
  code des paramètres (`SIEGE` par défaut). Portée « Société » possible (code = abréviation de la société).
- Attribution juste après l'enregistrement des éléments de sécurité (e-MCF, MCF par le poste, agent v4), dans
  une courte transaction : un problème de référence ne peut jamais annuler un fait fiscal. Année = année de la
  DEF Heure ; la série repart à 1 chaque année.
- Anti-doublons : ligne du compteur prise en exclusif (facture d'abord, jamais de verrou de plage) ; registre
  **DGI Sequence Entry** (ajout seul) dont la clé primaire est la référence et la facture une clé unique ;
  contrôle défensif du maximum du registre ; champ de la facture écrit par le SFE seulement (non copié, rétabli
  s'il est modifié par un formulaire ou par l'API).
- Échec d'attribution : retour au point de sauvegarde, événement **REFERENCE_EN_ATTENTE** (Critique), reprise par
  la tâche horaire `assign_missing` ou le bouton « Attribuer la référence séquentielle ».
- Point de vente › Référence › **Contrôler la série** (trous, doublons, écart avec les factures) et
  **Registre des références**.

### Impression
- « DGI Facture Normalisee » : la référence s'affiche **juste sous le numéro de facture**, en plus petit
  (8,5 px contre 11 px, gris), dans l'en-tête et dans « Informations générales ». Le bloc des éléments de
  sécurité DGI est inchangé ; sa mention indique désormais « le MCF » ou « l'e-MCF » selon le canal.
- « DGI Sales Invoice » (format v3/v4 conservé) : même ajout.

## Mise à jour

`bench migrate` puis `bench build --app dgi_compliance` (Frappe Cloud fait les deux au déploiement).
- Patch `v5_4.approbateur_role` (avant synchronisation) : crée le rôle DGI Approbateur.
- Patch `v5_4.sequence_pdv` : un code de séquence pour chaque point de vente fiscal ; paramètres (référence
  active, une série par point de vente, « Réf. PDV », `SIEGE`) ; date d'activation = date de la mise à jour.
  **Aucune numérotation rétroactive** : les factures normalisées avant la mise à jour ont déjà été imprimées sans
  référence.
- Après la mise à jour : vérifier les codes proposés (DGI Point of Sale › Code de séquence) avant la première
  facture, donner DGI Approbateur à la personne qui approuve à côté du responsable.

## Tests (bench Frappe 16.55 / ERPNext 16.54, simulateurs ; aucune facture réelle)

| Suite | Résultat |
|---|---|
| `test_sequence_pdv` (nouveau, Q01 à Q18) | 18/18 |
| `test_deux_utilisateurs` (nouveau, U01 à U09) | 9/9 |
| Suite complète de l'application | 166/166 |
| Mise à jour d'un site v5.3.2 avec données (`migrate`) | OK, 42 factures historiques non renumérotées |
| `dgi_mcf.js` (node) / `dgi_gateway` (pytest) | 15/15 / 48/48 |
| Navigateur réel : approbation par la caissière-approbatrice, champ sur la facture, impression | OK |
