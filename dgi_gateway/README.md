# DGI Gateway 0.2.0 — passerelle fiscale de site (MCF ↔ ERPNext)

> **Depuis dgi_compliance 5.2.0, la solution standard est le Poste MCF dans le navigateur** (page
> « Poste MCF » d'ERPNext dans Chrome/Edge sur la caisse où le MCF est branché) : rien à installer,
> pas de poste allumé en permanence. Ce programme reste utile (1) pour un site qui a un poste dédié
> allumé en permanence (DGI Gateway de type « Service (poste dédié) ») et (2) comme **outil de
> diagnostic** du MCF (`dgi-gateway discover`, `dgi-gateway status`).

Petit programme installé **sur un poste du point de vente fiscal**, branché au MCF physique
(USB / RS232, protocole MCF‑SFE v1.0, 115200 8N1). Il relie ce MCF à ERPNext (app
`dgi_compliance` ≥ 5.1.0), que ERPNext soit sur Frappe Cloud ou sur un serveur du client.

- **Une passerelle = un MCF = un point de vente fiscal.** Elle refuse de travailler avec un
  boîtier dont le NID n'est pas celui enregistré dans ERPNext.
- **Appels HTTPS sortants uniquement** vers le site ERPNext auquel elle est enrôlée : aucun port
  en écoute, rien à ouvrir sur le routeur du site.
- Chaque requête est **signée** (Ed25519, horodatage ±300 s, nonce à usage unique). La clé privée
  est créée sur le poste et ne le quitte jamais.
- Un **journal local** (SQLite, écriture synchrone) garde la trace de chaque facture envoyée au
  MCF ; après une coupure (courant, câble, réseau), la passerelle interroge le MCF (C1h) avant
  toute nouvelle facture et ne renormalise jamais une facture déjà enregistrée.

## 1. Prérequis

| Élément | Exigence |
|---|---|
| Poste | Linux (Ubuntu/Debian, Raspberry Pi OS) ou Windows 10/11, allumé pendant les heures de vente |
| Python | 3.10 ou plus récent (Windows : installé « for all users ») |
| MCF | branché en USB (port COM virtuel) ou RS232 |
| Réseau | HTTPS sortant vers le site ERPNext (port 443) |
| Horloge | synchronisée (NTP / heure Windows) : un écart de plus de 5 min fait refuser les requêtes |

## 2. Préparation dans ERPNext (une fois par point de vente)

Rôle requis : **DGI Fiscal Manager** (ou System Manager).

1. **DGI MCF Device** : créer le boîtier avec son **NID** (celui de l'étiquette / attestation DGI).
2. **DGI Point of Sale** : renseigner la société, le **canal principal = MCF** et le MCF rattaché.
   Le canal actif ne se modifie pas à la main : bouton **Canal › Changer de canal** (traçé).
3. **POS Profile** des caisses du site : champ « Point de vente fiscal DGI » = ce point de vente.
4. **DGI Gateway** : créer la passerelle, type **Service (poste dédié)** (identifiant, ex. `GW-LEMBA-01`, et point de vente),
   puis **Passerelle › Générer un code d'enrôlement**. Le code est affiché **une seule fois**,
   valable **30 minutes**, à usage unique. Le transmettre de vive voix à la personne sur site.

## 3. Installation

### Linux

```bash
cd dgi_gateway
sudo ./packaging/linux/install.sh
sudo dgi-gateway init --site https://VOTRE-SITE.frappe.cloud --gateway-id GW-LEMBA-01
sudo -u dgi-gateway dgi-gateway discover          # ports série qui répondent, avec NID et NIF
sudo -u dgi-gateway dgi-gateway enroll --code XXXX-XXXX-XXXX
```

`install.sh` crée le compte de service `dgi-gateway` (sans shell, membre de `dialout`), installe
le programme dans `/opt/dgi-gateway`, l'unité systemd durcie et un modèle de règle udev.
Pour un nom de port stable, compléter `/etc/udev/rules.d/99-dgi-mcf.rules` (identifiants USB du
MCF) et mettre `"serial_port": "/dev/dgi-mcf"` dans `/etc/dgi-gateway/gateway.json`.

### Windows (script non encore testé sur poste réel — voir § 8)

PowerShell **administrateur**, dans le dossier `dgi_gateway` :

```powershell
Set-ExecutionPolicy -Scope Process Bypass; .\packaging\windows\install.ps1
$G = "$env:ProgramFiles\DGI Gateway\venv\Scripts\dgi-gateway.exe"
& $G init --site https://VOTRE-SITE.frappe.cloud --gateway-id GW-LEMBA-01 --port COM3
& $G discover
& $G enroll --code XXXX-XXXX-XXXX
```

## 4. Approbation (ERPNext) puis démarrage

`enroll` affiche **l'empreinte de la clé** du poste (ex. `70A7-014F-9439-E4F2-EFD0`).
Dans ERPNext, sur la passerelle (statut « A approuver ») : **Passerelle › Approuver**, saisir
l'empreinte lue sur le poste. Si elle diffère, **ne pas approuver** : quelqu'un d'autre a utilisé
le code. Révoquer et générer un nouveau code.

Démarrage :

- Linux : `sudo systemctl enable --now dgi-gateway` puis `journalctl -u dgi-gateway -f`
- Windows : `Start-ScheduledTask -TaskName "DGI Gateway"` (journal :
  `C:\ProgramData\DGI Gateway\gateway.log`)

Contrôle : `dgi-gateway status` (état du MCF, connexion DGI du MCF, configuration reçue du serveur).
Dans ERPNext : **DGI Point of Sale › MCF › Etat du MCF**.

## 5. Fonctionnement côté caisse

Sur un point de vente au canal MCF, la validation d'une facture la place dans la file du MCF
(« Facture transmise au MCF, position n ») ; la facture se met à jour automatiquement dès que la
passerelle renvoie le code DEF, le compteur et le QR. Une seule facture à la fois est envoyée
à un même MCF ; les autres attendent leur tour.

| Situation | Ce qui se passe |
|---|---|
| Poste ou MCF éteint, câble débranché | les factures restent **en file** dans ERPNext ; rien n'est perdu ; alerte « passerelle muette » aux gestionnaires après 2 min |
| Coupure pendant une normalisation | l'opération passe « Uncertain » puis « Checking » : la passerelle lit la dernière facture du MCF (C1h) et règle le cas **sans double normalisation** |
| Internet du site coupé | les caisses n'atteignent plus ERPNext (cloud) : pas de vente ERPNext possible ; au retour, la passerelle reprend la file |
| Le MCF refuse (montant, taux, NIF…) | facture « Rejected » avec le code DGI ; corriger puis revalider |
| MCF non synchronisé avec la DGI depuis 7 jours | alerte quotidienne (exigence 6.1) ; bouton **Relancer la synchronisation DGI** (6.2) |

Passer un point de vente de MCF à e‑MCF (ou l'inverse) se fait uniquement par **Changer de canal**
(motif, référence DGI pour un repli, deux personnes si activé, aucune facture en cours).

## 6. Sécurité

- **Clé privée** : Linux `/var/lib/dgi-gateway/gateway.key` (mode 600, compte `dgi-gateway` ;
  la passerelle refuse de démarrer si le fichier est lisible par d'autres) ; Windows
  `C:\ProgramData\DGI Gateway\gateway.key` chiffrée DPAPI (portée machine), dossier réservé à
  SYSTEM et aux Administrateurs.
- **Ne jamais copier** `gateway.key` vers un autre poste. Poste perdu, volé ou réinstallé :
  **Passerelle › Révoquer** dans ERPNext, puis nouvel enrôlement.
- **Changement de clé** : nouveau code dans ERPNext, puis `dgi-gateway enroll --code … --rotate`
  et nouvelle approbation.
- Le compte ERPNext de la passerelle (`<id>@dgi-gateway.invalid`, rôle **DGI Gateway**) n'a pas
  accès au bureau ERPNext et ne peut agir que sur les opérations de son MCF.
- Les refus (signature invalide, rejeu, mauvais boîtier) sont journalisés dans
  **DGI Gateway Event** et signalés aux gestionnaires.

## 7. Fichiers

| | Linux | Windows |
|---|---|---|
| Configuration | `/etc/dgi-gateway/gateway.json` | `C:\ProgramData\DGI Gateway\gateway.json` |
| Clé, journal, état | `/var/lib/dgi-gateway/` | `C:\ProgramData\DGI Gateway\` |
| Programme | `/opt/dgi-gateway/` | `C:\Program Files\DGI Gateway\` |
| Journal d'exécution | `journalctl -u dgi-gateway` | `gateway.log` |

## 8. Limites connues (incrément 1)

- Validé sur le **simulateur MCF** et sur ERPNext 16 de test (HTTP réel) ; **pas encore sur un MCF
  physique** : à faire dès qu'un boîtier homologué est disponible.
- Windows : installation par tâche planifiée SYSTEM, script **non testé sur poste réel** ; service
  Windows natif et mises à jour signées prévus à l'incrément 2.
- Paiement à crédit (mode H) : non prévu par le protocole MCF v1.0 ; politique réglable dans
  DGI Compliance Settings (« Bloquer » par défaut).

## Dépannage

| Message | Cause probable | Action |
|---|---|---|
| `l'URL du site ERPNext doit commencer par https://` | URL en http | corriger `--site` |
| `HTTP 401` / `SIGNATURE_REFUSEE` | horloge du poste décalée, ou clé différente de celle approuvée | synchroniser l'heure ; sinon révoquer et réenrôler |
| `HTTP 403 passerelle A approuver` | approbation pas encore faite | approuver dans ERPNext |
| `NID_INATTENDU` | le boîtier branché n'est pas celui du point de vente | brancher le bon MCF (aucune facture n'est envoyée) |
| `… lisible par d'autres comptes` | droits du fichier de clé | `sudo chmod 600 /var/lib/dgi-gateway/gateway.key` |
| `MEMOIRE_PLEINE`, `MCF_BLOQUE` | état du boîtier | contacter le fournisseur du MCF / la DGI |

Autotest sans matériel : `dgi-gateway selftest`.
