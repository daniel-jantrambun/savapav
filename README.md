# Savapav

Planificateur d'entraînement personnel, hébergé sur le Synology DS720+ :

```
Garmin (export FIT / Garmin Connect) ─▶ SQLite ─▶ charge d'entraînement (CTL/ATL/TSB)
                                                     │
        analyse par sport + ce que vous voulez améliorer ─▶ planificateur déterministe ─▶ garde-fous
                                    (ou objectif course : distance, temps, date)
                                                                                            │
                                                         LLM 3B local (Ollama) ◀── plan JSON, semaine par semaine
                                                                │
                                                     texte des séances (ne modifie aucun chiffre)
```

Pas de chat : l'outil génère un plan, le LLM ne fait que le rédiger. Coût : ~0 €/an (électricité).

Multi-utilisateurs (famille), accessible uniquement via le réseau local ou le WireGuard du NAS : l'app n'est pas
exposée sur Internet (le nom de domaine optionnel, voir [Web Station](#portail-web-station),
pointe lui aussi vers le NAS en local ou dans le WireGuard).

Origine : conversation Le Chat « Utilisation de données sportives pour modèles d'AI » (oct. 2026),
résumée et revue dans [docs/architecture.md](docs/architecture.md).

## État d'avancement (roadmap)

| # | Étape | État |
|---|-------|------|
| 1 | Import des activités (FIT Garmin, sync Garmin Connect) → SQLite | ✅ code prêt, à tester sur les vraies données |
| 2 | Exploration : charge hebdo, CTL/ATL/TSB | ✅ écran Activités (forme / fatigue) |
| 3 | Modèle v1 : prédiction de performance (XGBoost) | ✅ temps estimés, vérification d'objectif, progression (écran Activités) |
| 4 | Planificateur v1 déterministe + garde-fous | ✅ course à pied (objectif daté) et multisport (blocs de 4 semaines) |
| 5 | Rédaction des séances par le LLM 3B | ✅ code prêt, à tester sur le NAS |
| 6 | Feedback (RPE) → ajustement du plan | ⏳ |
| 7 | Détection de fatigue, recalibrage mensuel | ⏳ |

## Interface web

React + Vite + TypeScript + Tailwind dans [web/](web/), servie par FastAPI (un seul conteneur). Pensée pour le
téléphone, en clair/sombre selon le réglage de l'appareil :

- **Mon plan** : pas d'objectif à fixer. L'app analyse vos 8 dernières semaines par sport (course, vélo, natation,
  salle, autre) et vous demande ce que vous voulez améliorer dans chacun (maintenir, endurance, vitesse, technique,
  force). Elle en tire un bloc de 4 semaines (3 de charge + 1 de décharge) ; le bloc suivant est recalculé à partir
  de ce que vous avez réellement fait. Le plan « course à une date » (distance, chrono) reste disponible.
  Semaines dépliables (semaine en cours mise en avant), rédaction par le LLM.
- **Activités** : fraîcheur du jour, courbes forme/fatigue, import de fichiers (en tâche de fond avec progression :
  un export complet prend plusieurs minutes), synchro Garmin (les 50 dernières activités, ou « Tout synchroniser » :
  tout l'historique, en tâche de fond avec progression, après confirmation). Liste des dernières activités, 50 par
  50 (« Afficher plus d'activités »).
- **Graphes** : par type d'activité (course, vélo, natation, salle, autre) sur 12 semaines, 6 mois, 1, 2, 3 ou 5 ans :
  volume, allure ou vitesse et FC moyenne par semaine, puis la liste des activités de ce type. Un clic sur une
  activité ouvre son récap : allure/vitesse, FC, altitude, puissance et cadence (survol synchronisé), temps par km
  (ou par 5 km à vélo), longueurs de bassin en natation (allure /100 m, comparable en 25 et 50 m ; tranches de 100 m),
  temps par zone cardiaque. Un fichier multisport (triathlon) donne une activité par sport, transitions exclues. Les courbes sont relues dans le
  FIT d'origine conservé sur le NAS (`data/fit`) : une activité dont le fichier a disparu n'affiche que son résumé.
- **Profil** : FC repos/max, connexion du compte Garmin (MFA compris), mot de passe.
- **Comptes** (admins) : liste des comptes et de leur état Garmin, création, suppression, nouveau mot de passe.

L'API est sous `/api` (docs : `/api/docs`).

## Comptes et Garmin

- **Premier lancement** : tant qu'aucun compte n'existe, l'app propose de créer le compte administrateur (une seule
  fois, création atomique). Ouvrez l'app juste après le premier déploiement pour le créer.
- **Pas d'inscription libre** : un admin crée, supprime les comptes et réinitialise les mots de passe (page
  **Comptes**). Il reste toujours au moins un admin, et un admin ne peut pas supprimer son propre compte.
  Supprimer un compte efface aussi ses activités, plans, fichiers FIT et jetons Garmin.
- Mots de passe hachés (scrypt), sessions par cookie HttpOnly (30 jours), blocage 15 min après 5 échecs.
  En secours, la CLI reste disponible : `coach add-user <nom> [--admin]`, `coach set-password <nom>`.
- **Garmin sans stocker le mot de passe** : chacun connecte son compte depuis l'app (`POST /me/garmin`, puis
  `/me/garmin/mfa` si le MFA est actif). Le mot de passe Garmin sert une seule fois à obtenir des jetons ; seuls
  ces jetons sont stockés, chiffrés avec `COACH_SECRET_KEY`. Quand ils expirent, la synchro le signale
  (`last_error`) et la personne se reconnecte.
- **Tout l'historique Garmin** : « Tout synchroniser » (ou `coach sync-garmin --user <nom> --full`) parcourt
  l'historique page par page, avec une pause de 1,5 s entre deux téléchargements pour ne pas être bloqué par Garmin
  (API non officielle) : compter 20 à 40 min pour 1 000 activités. Si Garmin limite les requêtes (429), ce qui a été
  téléchargé est importé et la synchro reprend où elle s'est arrêtée au prochain lancement. Chaque synchro n'importe
  que les fichiers nouveaux. Pour un premier import massif, l'export complet Garmin (ci-dessous) reste le plus sûr.
- **Sans Garmin** : import d'un `.fit` ou de l'export complet Garmin (`.zip`) via `POST /activities/import`, en
  tâche de fond (réponse immédiate, sans 504 du reverse proxy) ; avancement par `GET /activities/import`.
- Les données de chacun (activités, plans, FC repos/max) sont isolées par utilisateur.

## Développement local

```bash
python3.14 -m venv .venv && .venv/bin/pip install -e '.[dev]'
brew install libomp   # macOS : OpenMP pour XGBoost (sans lui, la prédiction garde la méthode simple)
.venv/bin/pytest
```

```bash
.venv/bin/coach add-user benjamin --name Benjamin --admin   # ou via l'écran de premier lancement
# Importer l'export complet Garmin ("Exporter vos données", le .zip tel quel)
.venv/bin/coach import-fit --user benjamin ~/Downloads/garmin-export
# Ce que vous faites, sport par sport, puis un bloc multisport de 4 semaines (sport absent = non planifié)
.venv/bin/coach analyze --user benjamin
.venv/bin/coach block --user benjamin --sessions 8 --run vitesse --bike endurance --swim technique --gym force
# Temps estimés (5 km → marathon), méthode retenue et son erreur mesurée, records
.venv/bin/coach performance --user benjamin
# Après mise à jour : relire les FIT déjà stockés (sous-sport, sessions vélo/course des fichiers multisport)
.venv/bin/coach reimport --user benjamin
# Ou un plan vers une course : semi en 1h45, 4 sorties/semaine
.venv/bin/coach plan --user benjamin --distance 21.1 --target 1:45:00 --race-date 2027-02-07 --runs 4
# Faire rédiger la 1re semaine par le LLM (Ollama local : ollama pull qwen2.5:3b)
.venv/bin/coach narrate --user benjamin 1 --week 0
# API (docs sur http://localhost:8000/api/docs)
.venv/bin/uvicorn coach.api:app --reload
```

```bash
# Interface web en dev (http://localhost:5173, /api relayé vers :8000)
cd web && npm install && npm run dev
npm run lint && npm run build
```

## Déploiement sur le NAS

Deux conteneurs ([compose.yaml](compose.yaml)) : `app` (FastAPI + front, SQLite dans `./data`) et `llm` (Ollama,
3 Go de RAM max). La CI pilote le NAS en SSH via WireGuard : le NAS tire lui-même les images (app sur ghcr.io,
Ollama sur Docker Hub) et le modèle.

### Workflows GitHub Actions

- **Test, build Docker image and deploy it on Synology** (à chaque push) : tests Python + lint/build du front, puis
  sur `main` : image `ghcr.io/daniel-jantrambun/savapav:latest`, copie du `compose.yaml`, `docker pull` de
  `latest` par le NAS (connexion à ghcr.io avec le jeton du job, déconnexion juste après), vérification que l'image
  tirée est bien celle du commit (sinon arrêt : `latest` n'est poussé que depuis `main`), `docker compose up -d`,
  nettoyage des anciennes images, déclaration du projet `savapav` dans Container Manager s'il n'existe pas (non
  bloquant), vérification de `/api/health`.
  Le déploiement est ignoré tant que la variable `SYNOLOGY_PRIVATE_IP` n'est pas configurée.
- **Install Ollama on Synology** (manuel) : le NAS tire l'image Ollama (version du `compose.yaml`) et télécharge
  le modèle (`ollama pull`, modèle choisi au lancement) dans `ollama/`, via un conteneur temporaire ; le service
  `llm` est relancé s'il est déjà déployé. À lancer avant le premier déploiement, puis pour changer de version ou
  de modèle.

Secrets et variables du dépôt (Settings → Secrets and variables → Actions), les mêmes que pour `cv-bd` :

| Nom | Type | Contenu |
|---|---|---|
| `WG_CONFIG` | secret | config WireGuard du runner |
| `SYNOLOGY_USER_PWD` | secret | mot de passe de l'utilisateur SSH du NAS |
| `COACH_SECRET_KEY` | secret | phrase secrète écrite dans le `.env` du NAS à chaque déploiement (voir [.env.example](.env.example)) |
| `SYNOLOGY_USER_LOGIN` | variable | utilisateur SSH du NAS |
| `SYNOLOGY_PRIVATE_IP` | variable | IP du NAS dans le WireGuard |
| `SAVAPAV_PORT` | variable (optionnelle) | port de l'app sur le NAS, écrit dans le `.env` (8000 par défaut) |

### Première installation

1. Créer le secret `COACH_SECRET_KEY` du dépôt. Le déploiement crée `/volume1/docker/savapav/.env` à partir de
   [.env.example](.env.example) s'il n'existe pas, y écrit le secret et le rend lisible par root uniquement.
2. Lancer le workflow **Install Ollama on Synology**.
3. Relancer le workflow de déploiement (ou pousser sur `main`). Ce premier déploiement crée le projet `savapav`
   dans Container Manager. Même si les conteneurs tournent déjà, Container Manager peut afficher le projet comme
   arrêté : dans **Container Manager → Projet → savapav → Action**, cliquer sur **Construire** (*Build*) pour
   qu'il passe à l'état **En cours d'exécution**. À ne faire qu'une fois.
4. Ouvrir l'app et créer le compte administrateur, puis les comptes de la famille (page **Comptes**).

L'app est alors sur `http://<ip-du-nas>:8000` (ou le port `SAVAPAV_PORT`), depuis le réseau local ou le WireGuard.

### Portail Web Station

Optionnel : Web Station peut relayer l'app, toujours depuis le réseau local ou le WireGuard. Deux modes :

- **Par port** (*Port-based*) : Web Station écoute sur un autre port du NAS, `8001` par exemple (pas le port de
  l'app, déjà pris), et relaie vers elle : `http://<ip-du-nas>:8001`. Pas de certificat TLS dans ce mode.
- **Par nom** (*Name-based*) : Web Station répond sur les ports 80/443 pour un hostname, par exemple
  `savapav.my-domain.com`, avec un certificat Let's Encrypt : `https://savapav.my-domain.com`. Le nom doit pointer
  vers l'IP du NAS (enregistrement DNS ou DNS du routeur).

1. Ajouter le projet aux portails web, dans **Container Manager → Project → savapav** :
   1. arrêter le projet (**Stop**) ;
   2. dans **Settings**, cocher **Set up web portal via Web Station**, puis choisir `app`, `8000` et `http`, et
      cliquer sur **Save** ;
   3. choisir le type de portail : **Port-based** avec le port `8001`, ou **Name-based** avec le hostname
      `savapav.my-domain.com` ;
   4. redémarrer le projet (**Start**).
2. Par nom uniquement, créer le certificat TLS dans **Control Panel → Security → Certificate** : **Add** → **Add a
   new certificate** → **Get a certificate from Let's Encrypt** → **Domain name** : `savapav.my-domain.com`,
   renseigner l'email, puis **Done**. Pas besoin de **Subject Alternative Name**. La création prend environ une
   minute. Let's Encrypt valide le domaine par le port 80 depuis Internet, à la création puis à chaque
   renouvellement (~90 jours).
3. Une fois l'app servie en HTTPS, passer `COACH_COOKIE_SECURE=true` dans `/volume1/docker/savapav/.env` (voir
   [.env.example](.env.example)), puis redémarrer le projet.

Synchro Garmin nocturne de tous les comptes connectés : tâche planifiée DSM (Panneau de configuration →
Planificateur de tâches, en root) :

```bash
cd /volume1/docker/savapav && /usr/local/bin/docker compose exec -T app coach sync-garmin --all
```

Sauvegarder `data/coach.sqlite` (Hyper Backup) et `COACH_SECRET_KEY` séparément.

## Configuration

Voir [.env.example](.env.example) : clé de chiffrement des jetons Garmin, cookie sécurisé, modèle LLM.
FC de repos / FC max (pour le TRIMP) : réglage par utilisateur (`PATCH /me`).
