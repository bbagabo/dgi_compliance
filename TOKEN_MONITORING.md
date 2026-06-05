# Surveillance d'expiration du jeton e-DEF

Le jeton JWT e-DEF expire (`tokenValid`). L'app surveille cette échéance via le **scheduler Frappe**,
qui tourne sur votre instance cloud et peut joindre la DGI — contrairement à un outil externe.

## Comment ça marche
- `hooks.py` enregistre **une seule** entrée quotidienne :
  `scheduler_events.daily → dgi_compliance.edef.tasks.check_token_expiry`.
- À l'exécution, le job lit **DGI Compliance Settings** et décide s'il doit s'exécuter aujourd'hui
  selon la **cadence choisie**. Vous changez la cadence sans redéployer.
- Il appelle `GET /api/info/status`, met à jour **Token Valid Until**, et si l'échéance est dans
  `warn_days_before` jours (ou si l'appel échoue / `status=false`), il **notifie par e-mail** et
  écrit une entrée d'audit (Error Log).

## Régler la cadence (Settings → Token Monitoring)
| Champ | Effet |
|---|---|
| **Check Frequency** | `Daily` (chaque matin), `Weekly`, ou `Monthly` |
| **Weekday (if Weekly)** | jour de la semaine où vérifier (ex. `Monday`) |
| **Day of Month (if Monthly)** | jour du mois (1–28) où vérifier |
| **Warn N Days Before Expiry** | seuil d'alerte (défaut 7) |
| **Notify Recipients** | e-mails séparés par virgule ; vide = tous les System Managers |

Exemples :
- *Chaque matin* : Frequency = `Daily`.
- *Chaque semaine le lundi* : Frequency = `Weekly`, Weekday = `Monday`.
- *Chaque mois le 1er* : Frequency = `Monthly`, Day of Month = `1`.

> Le scheduler `daily` de Frappe tourne une fois par jour (heure gérée par Frappe Cloud). La cadence
> hebdo/mensuelle est donc appliquée *par-dessus* ce déclencheur quotidien, dans le code.

## Prérequis
- **Email sortant** configuré (Settings → Email Account / Frappe Cloud Mail) pour recevoir les alertes.
- Le **Scheduler doit être actif** : sur Frappe Cloud il l'est par défaut. Vérifiez via
  *Scheduled Job Log* / *Scheduled Job Type* qu'il n'est pas en pause.

## Tester immédiatement (sans attendre le cron)
Depuis **bench console** (ou un bouton serveur) :
```python
from dgi_compliance.edef.tasks import check_token_expiry
check_token_expiry()
```
Puis consultez votre boîte mail et **Error Log** (`[DGI] ...`). `Token Valid Until` dans Settings
doit aussi se mettre à jour.

## Renouveler le jeton
1. Régénérez le jeton sur le portail e-MCF.
2. Settings → **e-DEF JWT Token** → collez la nouvelle valeur → Save.
3. Optionnel : relancez `check_token_expiry()` pour rafraîchir `Token Valid Until`.

## Alternative / complément : rappel côté Cowork
Si vous voulez en plus un rappel dans Cowork (chat), je peux créer une tâche planifiée Cowork qui
vous pingue (quotidien/hebdo/mensuel). Limite : elle tourne dans l'environnement Cowork et ne joint
pas la DGI — c'est un simple rappel, pas une vérification live. La vérification réelle reste le job
Frappe ci-dessus.
