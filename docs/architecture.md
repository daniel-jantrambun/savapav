# Architecture et décisions

Synthèse de la conversation Le Chat (canvas « Coach IA personnel — Architecture & Coûts »), avec les points
revus avant de démarrer.

## Décisions reprises telles quelles

- **Hébergement** : Synology DS720+ seul (Celeron J4125 4 cœurs, 6 Go RAM, 2 slots NVMe), accès par le
  réseau local ou le WireGuard existant (choix d'oct. 2026, plutôt que Tailscale ou Cloudflare Access).
  Cloudflare écarté : données de santé + GPS restent à la maison, et le Python tourne sans contrainte.
- **Pas de chat** : un outil de planification. Le plan est calculé par du code déterministe ; le LLM ne fait
  que rédiger les séances et justifier les ajustements, appelé à chaque recalcul de plan.
- **LLM** : 3B quantifié Q4 (Qwen 2.5 3B ou Llama 3.2 3B) via Ollama, ~2 Go de RAM, déchargé après inactivité.
  Un 7B est déconseillé tant que le NAS reste à 6 Go.
- **Garde-fous** : +10 % de volume par semaine max, 1 semaine de décharge toutes les 4, plafonds d'intensité.
- **Coût** : ~0 €/an (électricité ~10–15 €/an). Option : barrette 8 Go (~25 €) si un 7B devient utile.

## Points revus

1. **Une seule base SQLite, pas de PostgreSQL.** Un seul utilisateur, quelques milliers de lignes : un fichier
   SQLite (sur le volume NVMe si possible) suffit et retire un conteneur. Deux conteneurs au lieu de trois.

2. **Charge d'entraînement (CTL/ATL/TSB) avant XGBoost.** Le modèle fitness/fatigue de Banister (TRIMP à partir
   de la FC) est le standard du domaine, déterministe et explicable, et sert directement au planificateur
   (forme du jour, détection de surcharge). XGBoost (étape 3) arrive ensuite pour prédire la performance ; avec
   peu de courses officielles dans l'historique, la cible devra venir des meilleurs efforts (best efforts) sur
   les sorties, pas seulement des résultats de course.

3. **Import : privilégier l'export complet Garmin plutôt que l'API Strava.** L'API Strava est limitée à
   100 requêtes / 15 min et 1 000 / jour ; récupérer 1 000 activités avec leurs streams prend plusieurs jours.
   L'export « Exporter vos données » de Garmin donne tous les FIT d'un coup. La synchro quotidienne passe
   ensuite par `garminconnect` (non officiel : peut casser si Garmin durcit sa protection, comme pour
   `garminexport`).

4. **Le CPU du NAS est le vrai goulot pour le LLM.** Le J4125 n'a pas d'AVX/AVX2. Les « 3–6 tokens/s »
   annoncés sont optimistes, et surtout l'évaluation du prompt (un plan de 16 semaines en JSON) est lente.
   D'où : un appel par semaine avec un JSON compact, en tâche de fond, timeout de 10 min. Vérifier aussi que
   l'image `ollama/ollama` actuelle tourne bien sans AVX ; sinon basculer sur un `llama.cpp server` compilé
   sans AVX (même API OpenAI-compatible, il suffit de changer `COACH_LLM_BASE_URL`).

5. **Le LLM ne touche jamais aux chiffres, par construction.** Son texte est stocké à part (`plan_texts`), le
   plan JSON n'est jamais réécrit à partir de sa sortie.

6. **Multisport sans objectif chiffré (oct. 2026).** Plutôt que de fixer une course, l'app analyse ce que la
   personne fait et lui demande quoi améliorer, sport par sport. Le modèle de charge (TRIMP) couvrait déjà tous
   les sports ; voir « Planificateur multisport » ci-dessous.

## Planificateur v1

- Allures dérivées du temps visé par la formule de Riegel (exposant 1,06) : facile, sortie longue, seuil (~15 km),
  fractionné (~5 km), allure course.
- Phases : base (40 % des semaines de préparation, pas d'intensité lourde) → construction (fractionné + seuil)
  → affûtage (1 semaine, 2 pour le marathon) → semaine de course.
- Volume de départ = moyenne des 4 dernières semaines ; volume de pointe visé selon la distance
  (30 / 40 / 50 / 70 km pour 5 km / 10 km / semi / marathon), atteint seulement si la règle des +10 % le permet,
  sinon le plan l'indique dans `warnings`.
- `validate_plan` vérifie les garde-fous avant tout enregistrement.

## Planificateur multisport

- **Classement des séances** (`sports.py`) : champs `sport` / `sub_sport` des FIT → course, vélo, natation, salle
  (renfo, cardio en salle, HIIT) ou autre (rando, yoga…). « Autre » compte dans la fatigue, sans séances planifiées.
- **Analyse** sur 8 semaines glissantes : séances et minutes par semaine, tendance (4 dernières semaines contre les
  4 précédentes). Volume de référence = moyenne des 3 plus grosses des 4 dernières semaines (ignore une décharge).
- **Références sans objectif** : allures course par régression vitesse ~ % FC de réserve, lue au seuil (85 %) ;
  sinon sortie médiane = footing, seuil ~18 % plus rapide. Natation : allure médiane /100 m. Vélo : zones de FC
  (Karvonen). Salle : type de séance (bas / haut du corps, corps entier, gainage) et durée seulement.
- **Axes** : maintenir, endurance (volume + sortie longue), vitesse (fractionné / seuil), technique (natation),
  force (salle). Séances réparties depuis les habitudes, 2 minimum pour un sport à améliorer ; une séance ajoutée
  ajoute du temps (durée minimale), et la sortie longue n'est jamais plus courte qu'une séance habituelle.
- **Bloc de 4 semaines** : progression selon l'axe (+8 %/sem en endurance, +4 % en vitesse, 0 en maintien), puis
  décharge à 70 %, sans intensité. Le bloc suivant repart des activités réelles.
- **Garde-fous** (`validate_block`) : +10 % max par semaine (par sport, donc au total), 2 séances intenses max
  tous sports confondus, jamais à moins de 48 h, 2 séances max par jour, dernière semaine en décharge.

## Prédiction de performance (course à pied)

- **Meilleurs efforts** (`efforts.py`) : segment le plus rapide de 5 km, 10 km, semi et marathon de chaque
  course, relu dans le FIT (distance GPS cumulée, temps écoulé). Exclus : tapis et salle (distance de
  l'accéléromètre), sorties à plus de 6 m/s de moyenne (vélo ou voiture classés en course), segments traversant
  un saut du GPS (vitesse moyenne > 6 m/s sur 30 s, tunnels). 30 s et non 10 : en enregistrement « intelligent »
  (un point toutes les 6 à 8 s), le bruit normal (+30 m d'un point à l'autre) dépasse ce seuil sur 10 s.
  Table `best_efforts`, calculée en tâche de fond au premier affichage puis pour chaque nouvelle course
  (`activities.efforts_version` : incrémenter `EFFORTS_VERSION` relance le calcul). Un export Garmin (zip de zip)
  est ouvert une seule fois pour toutes ses activités : ~1 min pour 950 courses sur un Mac, quelques minutes sur
  le NAS. En CLI : `coach performance --user …`.
- **Cible** : les efforts « à fond », faute de courses officielles : à 2 % près du meilleur équivalent 10 km
  (Riegel) sur ± 45 jours, et à au moins 75 % de la FC de réserve quand elle est connue.
- **Méthode simple** : meilleur effort ≥ 5 km des 180 derniers jours, extrapolé par Riegel (exposant 1,06),
  pénalisé de 0,02 %/jour d'ancienneté. Pas de 1 km, ni en record ni en prédiction : une descente suffit à le
  fausser, et Riegel en extrapole des temps trop rapides.
- **XGBoost** (API native, sans scikit-learn) : apprend l'écart (log) entre l'effort réel et la méthode simple, à
  partir de la charge (CTL/ATL/TSB), du volume sur 4 et 12 semaines, de la plus longue sortie sur 8 semaines, de
  l'efficacité cardiaque (vitesse par battement au-dessus du repos) et de la distance. 200 arbres de profondeur 3.
- **Choix honnête** : validation chronologique (entraîné avant 60, 70, 80, 90 % des efforts, testé sur les 10 %
  suivants). XGBoost n'est retenu que s'il réduit l'erreur moyenne d'au moins 3 % et avec au moins 60 efforts
  cibles ; l'écran indique la méthode et son erreur mesurée. Sur l'historique de départ (950 courses, 74 efforts
  cibles) : 3,5 % d'erreur pour la méthode simple, 3,6 % pour XGBoost → méthode simple.
- **Vérification d'objectif** : progression jugée atteignable de 1 % par mois de préparation (8 % au plus) ;
  ambitieux en deçà, prudent si l'objectif est plus de 3 % plus lent que le temps estimé du jour.
- **Dépendance** : `xgboost-cpu` sous Linux (~6 Mo, OpenMP inclus, sans AVX : compatible J4125) ; sous macOS,
  `xgboost` demande `brew install libomp`, sans quoi l'app garde la méthode simple.

## Multi-utilisateurs (famille)

- Comptes créés par l'admin, mot de passe scrypt, sessions côté serveur (seul le hash du jeton est en base).
- L'app reste privée (réseau local / WireGuard) : elle détient des jetons Garmin et des données de santé.
- Garmin n'a pas d'API officielle ouverte aux particuliers (Connect Developer Program réservé aux entreprises).
  On utilise `garminconnect` ≥ 0.3.5 (nouvelle connexion « app mobile » ; l'ancienne via `garth` est cassée
  depuis 2026, et < 0.3.5 a la CVE-2026-54447). Le mot de passe est échangé contre des jetons, chiffrés en base
  (Fernet, clé dérivée de `COACH_SECRET_KEY`) ; il n'est jamais stocké.
- Strava écarté : ses conditions d'API interdisent depuis fin 2024 l'usage des données dans des modèles d'IA.
- SQLite suffit : quelques utilisateurs, très peu d'écritures concurrentes (WAL + busy_timeout). PostgreSQL ne
  deviendrait utile que pour une app publique.
