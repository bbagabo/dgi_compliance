# Agent MCF local — DGI Compliance v4.0

Pont entre le **MCF physique** (port COM virtuel USB, protocole MCF-SFE v1.0) et votre
site **ERPNext / Frappe Cloud** en mode « MCF Physique (USB) ».

```
ERPNext (cloud) ──HTTPS (token API)──> Agent MCF (ce PC) ──série 115200 8N1──> MCF (USB)
```

Le serveur cloud ne peut pas accéder à un port USB local : c'est l'agent qui détecte le
MCF, déroule le protocole (C1h → C3h → C0h → 31h… → 33h → 35h → 38h) et renvoie les
éléments de sécurité (Code DEF/SIG, NIM/MID, compteurs, heure, QR) au site.

## 1. Installation (PC Windows où le MCF est branché)

```powershell
# 1) Python 3.10+ (cocher "Add python.exe to PATH" a l'installation) : https://www.python.org
# 2) Dependances :
cd "C:\dgi\mcf_agent"        # dossier ou vous avez copie mcf_agent/
pip install -r requirements.txt
# 3) Pilote USB du MCF : installer le pilote du fabricant (le MCF doit apparaitre
#    dans le Gestionnaire de peripheriques > Ports (COM et LPT), ex. COM3)
```

## 2. Créer l'utilisateur API côté ERPNext (une fois)

1. Desk → **Users** → New : `mcf-agent@votredomaine.cd` (utilisateur API, pas humain).
2. Onglet Roles : ajouter **DGI MCF Agent** (créé par l'app v4).
3. Settings → **API Access** → *Generate Keys* : notez **API Key** et **API Secret**.

## 3. Configurer l'agent

```powershell
Copy-Item mcf_agent_config.example.json mcf_agent_config.json
notepad mcf_agent_config.json
```

| Champ | Valeur |
|---|---|
| `site_url` | `https://votre-site.frappe.cloud` |
| `api_key` / `api_secret` | clés générées à l'étape 2 |
| `agent_id` | nom du poste (ex. `CAISSE-01`) |
| `port` | `null` = auto-détection (sondage C1h) ; sinon `"COM3"` |

## 4. Tester puis lancer

```powershell
python mcf_agent.py --test     # détecte le port, interroge C1h/C2h, heartbeat vers le site
python mcf_agent.py            # boucle de service (Ctrl+C pour arrêter)
```

Dans ERPNext : **DGI Compliance Settings** → bouton **MCF → État de l'agent MCF** doit
montrer un heartbeat récent (vert) avec MID/NIF du dispositif.

## 5. Démarrage automatique (service Windows via Tâche planifiée)

```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument '"C:\dgi\mcf_agent\mcf_agent.py"' -WorkingDirectory "C:\dgi\mcf_agent"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName "DGI MCF Agent" -Action $action -Trigger $trigger -RunLevel Highest -Description "Agent MCF DGI Compliance"
Start-ScheduledTask -TaskName "DGI MCF Agent"
```

## 6. Diagnostic

- Journal : `mcf_agent.log` (même dossier).
- `python mcf_agent.py --test --verbose` : trames et octets de statut détaillés.
- « MCF introuvable » : vérifier câble USB, pilote COM, qu'aucun autre logiciel n'occupe
  le port, puis forcer `"port": "COMx"` dans la config.
- « site en mode E-MCF » : le sélecteur dans DGI Compliance Settings n'est pas sur
  **MCF Physique (USB)** — l'agent reste volontairement en veille (exclusion mutuelle).
- Alerte « dernière connexion DGI > 1 jour » (C2h) : le MCF n'arrive plus à transmettre
  à la DGI — vérifier sa connectivité réseau/GSM ; l'émission de factures reste possible
  selon les règles DGI mais faites-le remonter au fournisseur du dispositif.

## 7. Tests sans matériel

```powershell
python test_mcf_agent.py       # 12 tests: framing/BCC, NAK/SYN, flux complet simule
```
