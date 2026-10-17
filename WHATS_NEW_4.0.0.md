# DGI Compliance v4.0.0 — Notes de version

Version majeure : prise en charge du **MCF physique** (Machine de Contrôle Fiscal, USB)
en plus du mode e-MCF (API), avec **exclusion mutuelle stricte** des deux canaux.

## 1. Sélecteur de mode (DGI Compliance Settings)

- Nouveau champ **Mode de communication DGI** : `E-MCF (API)` (défaut) ou
  `MCF Physique (USB)`. Bandeau permanent indiquant le canal actif et le canal bloqué.
- Champs de connexion e-DEF (URL, token…) masqués en mode MCF ; nouvelle section
  **MCF Physique (USB)** : port COM optionnel, tolérance totaux (33h), délai de reprise
  des jobs, état de l'agent (dernier heartbeat + diagnostic C1h/C2h).

## 2. Blocage total du canal inactif (aucune double communication)

- **Garde matérielle dans `edef/client.py`** : en mode MCF, `call()` refuse TOUT échange
  HTTP vers la DGI avant même de construire la requête (aucun webhook, aucune API).
- `check_token_expiry` et `scheduled_sync` inertes en mode MCF.
- Réciproquement, les endpoints agent (`pull_jobs`, `push_result`) sont inertes en mode
  e-MCF ; un résultat MCF reçu après re-bascule est **refusé** et journalisé.
- Bascule de mode : message d'avertissement + décompte des jobs MCF en attente.

## 3. Mode MCF Physique — architecture

- Serveur (Frappe Cloud) : la normalisation crée un **DGI MCF Job** (file d'attente,
  payload JSON complet dérivé du mapper e-DEF — mêmes règles strictes de mapping taxes
  et paiements, remises, avoirs FA/EA + nature COR/RAN/RAM/RRR, USD CRT/CDT, commentaires A–H).
- **Agent MCF local** (`mcf_agent/`) sur le PC Windows où le MCF est branché :
  - **détection automatique du port USB** (sondage C1h de chaque port COM) ;
  - **interrogation du dispositif** : C1h (MID, NIF, compteurs, taux A–P) et C2h
    (connexion serveur DGI, alerte si > 1 jour) remontés au site (heartbeat) ;
  - protocole MCF-SFE v1.0 complet : trames SOH/LEN/SEQ/BCC(somme 16 bits, 4 ASCII)/ETX,
    NAK (retransmission même SEQ), SYN (attente), échappement des caractères spéciaux ;
  - flux facture : C3h client → C0h ouverture → 31h articles (remise = PRORIG,PRDESC) →
    36h commentaires → **33h contrôle des totaux** (écart > tolérance ⇒ annulation 38h C)
    → 35h paiements (E/V/C/M/D/A) → 38h normalisation (gestion réponse P: = patienter) ;
  - garde-fous fiscaux : NIF machine ≠ NIF facture ⇒ refus ; taux machine ≠ taux
    configuré (DGI Tax Group) ⇒ refus ; facture restée ouverte ⇒ annulation de sécurité.
- Résultat 38h appliqué sur la facture : Code DEF (SIG), NIM (MID), compteurs `FC/TC FT`,
  heure `jj/MM/aaaa HH:mm:ss`, **QR `RDCDEF01;MID;SIG;NIF;DT`** — le format d'impression
  (ORIGINAL/DUPLICATA inclus) fonctionne à l'identique dans les deux modes.

## 4. Nouveaux objets

- DocType **DGI MCF Job** (Queued / In Progress / Done / Error / Cancelled, reprise
  automatique des jobs bloqués, bouton re-mise en file).
- Rôle **DGI MCF Agent** (utilisateur API de l'agent) — fixture + patch `v4_0`.
- Endpoints REST `dgi_compliance.mcf.api.{heartbeat, pull_jobs, push_result, requeue_job}`.

## 5. Tests

`mcf_agent/test_mcf_agent.py` : 12 tests sans matériel (BCC conforme à l'exemple 12ABh
du protocole, NAK/SYN, flux complet sur MCF simulé, refus NIF/taux/totaux divergents,
avoir + USD, tolérance à l'ambiguïté FN/SIG de la réponse 38h).

## Référence

Basé sur « Spécifications techniques des dispositifs électroniques fiscaux — PROTOCOLE
MCF-SFE v1.0 » (DGI RDC) et sur le prototype E-MCF fourni (passerelle USB ⇄ ERP),
dont l'agent v4 reprend l'architecture (scénario, annulation de sécurité, détection port).
