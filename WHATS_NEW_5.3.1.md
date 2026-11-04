# DGI Compliance 5.3.1 - « Rechercher un MCF (sur ce PC) » et premier essai sur un MCF physique

Branche `feature/HL2-v5.3.1-recherche-mcf`, 4 novembre 2026, à partir de `main` (v5.3.0).

## Pourquoi

Dans DGI Compliance Settings, le bouton « Rechercher un MCF (sur la caisse) » de la v5.3.0 n'affichait
qu'un message : ERPNext, hébergé dans le cloud, ne voit pas les ports USB. Un responsable qui avait
branché le MCF sur son propre PC (COM8) ne pouvait donc rien détecter depuis cet écran.

## Ce qui change

1. **Bouton « Rechercher un MCF (sur ce PC) »** (menu MCF de DGI Compliance Settings), réservé à
   DGI Fiscal Manager / System Manager :
   - le navigateur du PC où le MCF est branché lit le port choisi (Web Serial, Chrome ou Edge en HTTPS) :
     C1h (état), 2Bh I0 à I4 (contribuable), C2h (connexion DGI, sans RESTART). Aucune commande fiscale ;
   - un dialogue affiche le port, le MID et le NID, le NIF (rapproché de la société qui porte ce NIF),
     la date du MCF, les compteurs, les groupes de taxation, l'état, la connexion DGI et le contribuable ;
     il indique si ce MCF existe déjà dans ERPNext ;
   - « Proposer ce MCF », avec un point de vente prévu facultatif, crée ou complète la fiche DGI MCF Device
     à l'état **Détecté**. Le MCF n'est **jamais activé** à cette étape : la validation reste explicite
     (DGI MCF Device › Valider ce MCF, par une autre personne si la règle des deux personnes est active) ;
   - messages clairs : navigateur sans port série (Firefox, Safari, HTTP), port déjà utilisé,
     port qui ne répond pas comme un MCF, réponse sans NID.
2. **Serveur** : `fiscal.registry.propose_from_browser` (POST, rôles fiscaux, 20 appels par heure) ;
   refus tracé (MCF_REFUSE) si le MCF est déjà actif sur un autre point de vente ; l'observation garde
   l'origine `browser-desk` et le point de vente prévu.
3. **C2h conforme au protocole SFE-MCF v1.0** (constaté sur le MCF physique) : le champ facultatif ER
   (« Operation terminee! ») est le dernier message du fabricant, pas une erreur de lecture. Le dialogue et
   la page Poste MCF affichent l'état (DIS, CON, TRA, RES), les transactions envoyées et **disponibles dans
   le MCF** (DC), et la **dernière connexion réussie à la DGI**, en rouge au-delà d'un jour (exigence du
   protocole, commande C2h).
4. **Outil de diagnostic Windows** `dgi_gateway/windows/Test_MCF_port.ps1` : lecture seule (C1h, 2Bh, C2h)
   d'un port COM avec Windows PowerShell, sans Python ni navigateur ; journal écrit à côté du script.
5. Tests : `test_routage_dgi` ne suppose plus l'abréviation « DTS » de la société de test.

## Essai sur le MCF physique (04/11/2026, COM8, lecture seule)

| Commande | Résultat |
|---|---|
| Port | COM8, « USB Serial Device », USB 03EB:6119, 115200 bit/s 8N1, réponse en 449 ms au premier essai |
| C1h | MID ED02000853-1 (NID ED02000853), NIF A0700190Q ; 3 transactions, 0 facture de vente, 0 avoir ; 16 groupes (A 0, B 16, C 8, F 16, G 8, autres 0) ; aucun indicateur d'état défini levé |
| 2Bh | I0 PREMIUM FOODS SPRL ; I1 adresse ; I2 « Monusco » ; I3 « LIMETE » ; I4 un numéro de téléphone |
| C2h | état DIS (non connecté au réseau), 3 envoyées, 3 disponibles, dernière connexion réussie 04/11/2026 17:10:03, message « Operation terminee! » |

Points relevés, à traiter hors de l'application :
- 2Bh : selon le protocole v1.0, I2 = ville, I3 = téléphone, I4 = e-mail. Le MCF est paramétré autrement
  (ville « Monusco », téléphone « LIMETE », e-mail = numéro de téléphone). Correction à demander au
  fournisseur du MCF (le protocole l'indique pour toute modification de ces informations). ERPNext lit
  ces champs selon le protocole.
- L'horloge du MCF retardait d'environ 3 minutes sur celle du PC.
- Le MCF était hors réseau (DIS) au moment de l'essai.

## Mise à jour

Aucune migration de données. `bench migrate` puis `bench build --app dgi_compliance` (nouveau JS des
paramètres et de la page Poste MCF).

## Tests (environnement de test, simulateurs ; aucune facture réelle)

| Suite | Résultat |
|---|---|
| `test_routage_dgi` (dont D07 à D09 nouveaux : proposition depuis les paramètres, réservée aux responsables, refus si actif ailleurs) | 31/31 |
| Suite complète de l'application (HL1 + HL2 + routage) | 130/130 |
| Navigateur réel, recherche depuis les paramètres (`tests/js/e2e_desk_search.js`, nouveau) : port muet, MCF lu (C1h, 2Bh champ par champ, C2h avec ER), proposition « Détecté », aucune facture, navigateur sans Web Serial | OK (17 contrôles) |
| Navigateur réel, découverte (`tests/js/e2e_discovery.js`) | OK |
| Navigateur réel, secours e-MCF -> MCF (`tests/js/e2e_station.js`) | OK |
| `dgi_gateway` (pytest) / `dgi_mcf.js` (node) | 48/48 / 15/15 |
| Installation neuve / mise à jour depuis une sauvegarde v4 | OK / OK |
| MCF physique sur COM8, lecture seule (`Test_MCF_port.ps1`, Windows PowerShell 5.1) | OK |

Les scripts `e2e_*.js` prennent le nom du site dans la variable `SITE_NAME` (par défaut `test.local`).

Non testé : la recherche depuis les paramètres avec le MCF physique dans Chrome (la fenêtre de choix du
port est une fenêtre du navigateur, à cliquer par l'utilisateur) ; e-MCF réel de la DGI.
