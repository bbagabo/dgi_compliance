# DGI Compliance v5.1.0 (HL2, incrément 1) — Notes de version

Base : v5.0.5. Objet : interfacer ERPNext avec le **MCF physique** en plus de l'e‑MCF
(note de service n° 01/220/DGI/DG/DLCFI/FMM/LLM/2026, § VI.2), sans modifier le cœur
d'ERPNext/Frappe. Architecture : document « HL2 – Architecture de communication SFE ↔ MCF ».

> **Branche de travail.** Cette version reste sur `feature/HL2-mcf-gateway` tant qu'elle n'a pas
> été essayée avec un MCF physique homologué. Ne pas la fusionner dans `main` avant.

## Ce qui change

### Canal fiscal par point de vente (et non plus global)
- **DGI Point of Sale** : société, **canal principal** (e‑MCF ou MCF), **canal actif** (lecture
  seule), MCF rattaché, autorisation de repli.
- Le canal actif ne change que par un **DGI Channel Switch** (document soumis : motif, référence
  DGI obligatoire pour un repli, deux personnes si activé, refusé tant qu'une facture est en cours
  au MCF ; les factures encore en file sont annulées et à revalider).
- Chaque facture porte son point de vente fiscal (repris du POS Profile), son canal et son
  opération : une caisse de Lemba ne peut utiliser que le MCF de Lemba.
- **Correctifs multi‑sites de la v5.0.5** : le jeton e‑MCF utilisé est celui du point de vente
  (et non plus le jeton global) ; le NIF par défaut n'est appliqué que si le site n'a qu'une société.

### File MCF côté serveur (DGI Fiscal Operation)
- Valider une facture d'un point de vente au canal MCF la place dans la **file de son MCF**
  (« Facture transmise au MCF, position n ») ; elle est mise à jour automatiquement (temps réel)
  quand la passerelle renvoie code DEF, compteurs et QR.
- **Une seule opération à la fois par MCF** (barrière). Bail limité dans le temps ; un résultat
  perdu passe « Uncertain » puis « Checking » : la passerelle relit la dernière facture du MCF
  (C1h) et règle le cas **sans double normalisation**.
- Résultats idempotents, contrôle de continuité des compteurs, contrôle du NID/NIF annoncé :
  toute incohérence met l'opération **en litige** (jamais d'éléments de sécurité saisis à la main).
- Brouillon modifié pendant qu'il attend en file : l'opération est annulée ; une fois transmise
  au MCF, la facture est gelée.

### Passerelles de site (DGI Gateway) et sécurité
- Nouveau programme **`dgi_gateway/`** (0.1.0) installé sur un poste du site, un par MCF : port
  série/COM USB, protocole MCF‑SFE v1.0 complet, journal local, appels HTTPS **sortants
  uniquement**. Voir `dgi_gateway/README.md`.
- Identité **Ed25519 propre à chaque passerelle** (aucune clé partagée), requêtes signées
  (horodatage ±300 s, nonce à usage unique), enrôlement par **code unique de 30 min** puis
  **approbation humaine avec comparaison d'empreinte**, révocation immédiate.
- Utilisateur technique par passerelle (rôle **DGI Gateway**, sans accès au bureau) ; refus et
  anomalies journalisés dans **DGI Gateway Event** (append‑only) et notifiés.
- Nouveau rôle **DGI Fiscal Manager** (approbation des passerelles, changements de canal, litiges).

### Exigences SFE couvertes par ce lot
- 6.1 : alerte quotidienne si le MCF n'est pas synchronisé avec la DGI depuis 7 jours
  (avertissement dès 1 jour, réglable).
- 6.2 : bouton **Relancer la synchronisation DGI** (commande C2h RESTART).
- Alerte « passerelle muette » après 2 min sans signal (réglable).

### Paramètres (DGI Compliance Settings)
- « Mode de communication » devient **« Canal par défaut (nouveaux points de vente) »**.
- Nouveaux : politique pour le paiement **à crédit** au MCF (« Bloquer » par défaut — le mode H
  n'existe pas dans le protocole MCF v1.0), durée du bail, tentatives max, délai d'attente DGI,
  intervalle d'interrogation, seuils d'alerte, règle des deux personnes.

### Retrait de l'agent MCF v4
- L'ancien agent v4 (`mcf_agent`, DGI MCF Job) est **retiré** : il n'est plus servi et la mise à
  jour annule ses travaux encore en file. Un site qui l'utilisait passe en e‑MCF jusqu'à
  l'installation et l'approbation de sa passerelle, puis un **Changer de canal › Mise en service**.

## Mise à jour

- Patch `v5_1.hl2_migrate` : société renseignée sur les points de vente (site mono‑société),
  canal actif = e‑MCF, canal principal = ancien mode global ; travaux v4 en file annulés.
- Nouvelle dépendance Python : `cryptography>=41` (déjà présente sur Frappe 16).
- Aucun changement pour un site 100 % e‑MCF : ses factures continuent comme en v5.0.5.

## Tests (banc ERPNext 16.53 / Frappe 16.54, Python 3.14, MariaDB 10.11)

| Lot | Résultat |
|---|---|
| Passerelle — tests unitaires (protocole, normalisation, reprises, NAK/SYN, P:, C1h, service) | 37/37 |
| HL2 — intégration Frappe (H01–H17 : routage Lemba/Ngaliema, barrière, litiges, signature, rejeu, mauvais boîtier, changement de canal) | 17/17 |
| HL1 — non‑régression v5.0.5 | 60/61 (T25 : rendu PDF wkhtmltopdf du banc, échoue aussi sur la v5.0.5 d'origine) |
| Montant en lettres | 12/12 |
| Bout en bout HTTP réel : enrôlement, refus avant approbation (403), approbation par empreinte, normalisation de factures, rejeu / corps modifié / clé intruse refusés (401) et journalisés | OK |
| Paquet installé (`install.sh`, compte de service, droits 600/640) parlant au simulateur par un vrai port série virtuel (pyserial) | OK — a révélé et corrigé un défaut : un port vide dans ERPNext effaçait le port configuré sur le poste |

## Limites connues (incrément 2)

- **Pas encore testé avec un MCF physique** homologué.
- Écran **Point de Vente (POS)** d'ERPNext : la vente au canal MCF n'y est pas encore branchée
  (factures du bureau uniquement).
- Windows : script d'installation (tâche planifiée SYSTEM) non testé sur poste réel ; service
  Windows natif et mises à jour signées à venir.
- Rotation de clé en ligne, résolution assistée d'un litige « normalisée au MCF ».
- Charge Frappe Cloud : à 2 s d'intervalle, une passerelle fait ~43 000 requêtes par jour
  (≈ 9 ms chacune mesurées sur le banc). À vérifier par rapport au plan du client ; l'intervalle
  se règle dans les paramètres ; un réveil par notification est prévu.
