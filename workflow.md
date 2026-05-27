# Workflow Frontend ↔ Backend PARALLAX

Guide destiné au développeur frontend. Décrit **chaque appel API**, dans quel ordre
le faire, ce qu'il retourne et **ce que cela représente pour l'utilisateur final**.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Conventions communes](#2-conventions-communes)
3. [Authentification](#3-authentification)
4. [Parcours Chercheur — soumettre un calcul](#4-parcours-chercheur--soumettre-un-calcul)
5. [Parcours Chercheur — suivre et récupérer les résultats](#5-parcours-chercheur--suivre-et-récupérer-les-résultats)
6. [Parcours Gestionnaire — tableau de bord cluster](#6-parcours-gestionnaire--tableau-de-bord-cluster)
7. [Parcours Gestionnaire — gérer les programmes](#7-parcours-gestionnaire--gérer-les-programmes)
8. [API Interne — agents C du cluster](#8-api-interne--agents-c-du-cluster)
9. [Stratégie de gestion des tokens JWT](#9-stratégie-de-gestion-des-tokens-jwt)
10. [Codes d'erreur et gestion des cas limites](#10-codes-derreur-et-gestion-des-cas-limites)

---

## 1. Vue d'ensemble

```
┌──────────────────────────────────────────────────────────┐
│                    Interface Web (Next.js)                │
│  Chercheur : dépose son code, suit l'exécution           │
│  Gestionnaire : supervise le cluster                     │
└─────────────────────────┬────────────────────────────────┘
                          │  HTTPS / JWT
                          ▼
┌──────────────────────────────────────────────────────────┐
│              Backend Flask (ce dépôt)                    │
│  /api/auth/*      Authentification                       │
│  /api/tasks/*     Projets chercheur                      │
│  /api/nodes/*     Nœuds cluster (gestionnaire)           │
│  /api/programmes/* Tous les programmes (gestionnaire)    │
│  /api/cluster/*   API interne agents C                   │
│  /api/docs/       Swagger UI                             │
└──────────┬───────────────────────────────────────────────┘
           │  X-Cluster-Key
           ▼
┌──────────────────────────────────────────────────────────┐
│              Agents C (code C sur chaque nœud)           │
│  Agent Maître   : décompose les programmes               │
│  Agent Worker   : exécute les sous-tâches                │
│  Agent Contrôleur : gossip & surveillance                │
└──────────────────────────────────────────────────────────┘
```

### Deux rôles utilisateur

| Rôle          | Ce qu'il fait dans l'UI                                          |
|---------------|------------------------------------------------------------------|
| `chercheur`   | Dépose du code source, soumet pour calcul, télécharge résultats |
| `gestionnaire`| Surveille les nœuds, inspecte tous les programmes, annule       |

---

## 2. Conventions communes

### URL de base

```
http://<ip-nœud-maître>:5000
```

### Format de toutes les réponses

**Succès :**
```json
{
  "success": true,
  "message": "...",   // optionnel
  "data": { ... }     // null ou objet/tableau
}
```

**Erreur :**
```json
{
  "success": false,
  "error": "Message lisible par l'humain.",
  "details": null    // optionnel
}
```

### En-têtes requis

```http
Authorization: Bearer <access_token>    // toutes les routes /api/auth, /api/tasks, /api/nodes, /api/programmes
X-Cluster-Key: <clé_secrète>           // uniquement /api/cluster/*
Content-Type: application/json          // requêtes JSON
```

---

## 3. Authentification

### 3.1 Inscription

**Ce que voit l'utilisateur :** Formulaire "Créer un compte" avec champ rôle.

```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "alice_dupont",
  "email": "alice@enspy.cm",
  "password": "s3cr3t!Pass",
  "role": "chercheur"          // ou "gestionnaire"
}
```

**Réponse 201 :**
```json
{
  "success": true,
  "message": "Compte créé avec succès.",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "alice_dupont",
    "email": "alice@enspy.cm",
    "role": "chercheur",
    "is_active": true,
    "storage_used_bytes": 0,
    "created_at": "2024-09-01T08:00:00Z",
    "last_login_at": null
  }
}
```

**Erreurs à gérer :**
- `400` — format username/email/password invalide
- `409` — username ou email déjà pris

**Action frontend :** Rediriger vers la page de connexion après succès.

---

### 3.2 Connexion

**Ce que voit l'utilisateur :** Formulaire "Se connecter" (username ou email + mot de passe).

```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "alice_dupont",   // OU "email": "alice@enspy.cm"
  "password": "s3cr3t!Pass"
}
```

**Réponse 200 :**
```json
{
  "success": true,
  "message": "Connexion réussie.",
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "user": {
      "id": "550e8400...",
      "username": "alice_dupont",
      "role": "chercheur",
      ...
    }
  }
}
```

**Action frontend :**
1. Stocker `access_token` (mémoire ou sessionStorage)
2. Stocker `refresh_token` (cookie httpOnly recommandé)
3. Lire `data.user.role` pour décider quelle interface afficher :
   - `chercheur` → page "Mes projets"
   - `gestionnaire` → page "Tableau de bord cluster"

---

### 3.3 Vérification de session au démarrage

**Ce que voit l'utilisateur :** L'application vérifie si la session précédente est encore valide.

```http
GET /api/auth/me
Authorization: Bearer <access_token>
```

**Réponse 200 :** Le profil de l'utilisateur (même structure que `user` ci-dessus).

**Erreur 401 :** L'access token est expiré → déclencher le rafraîchissement (voir §9).

---

### 3.4 Déconnexion

**Ce que voit l'utilisateur :** Bouton "Se déconnecter".

```http
POST /api/auth/logout
Authorization: Bearer <access_token>
```

**Action frontend :**
1. Appeler cet endpoint (révoque le token côté serveur)
2. Supprimer les tokens stockés localement
3. Rediriger vers la page de connexion

---

## 4. Parcours Chercheur — soumettre un calcul

### 4.1 Importer le code source

**Ce que voit l'utilisateur :** Formulaire "Nouveau projet" avec upload de fichier.

Le chercheur dépose un fichier source (`.py`, `.c`, `.cpp`, `.java`, …) ou une archive
(`.zip`, `.tar.gz`) contenant son code annoté avec les directives PARALLAX
(`@parallax.split`, `@parallax.dag`, `@parallax.shared`).

```http
POST /api/tasks/import
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

file=<fichier_binaire>
name=Simulation Monte-Carlo turbulence
description=Modèle de Navier-Stokes simplifié
```

**Réponse 201 :**
```json
{
  "success": true,
  "message": "Programme importé avec succès.",
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "Simulation Monte-Carlo turbulence",
    "status": "soumis",
    "original_filename": "simulation.zip",
    "source_size_bytes": 524288,
    "uploaded_at": "2024-09-15T14:00:00Z",
    ...
  }
}
```

**Points importants :**
- Stocker `data.id` — il sert pour tous les appels suivants
- Le statut est `soumis` : le fichier est stocké mais **l'exécution n'a pas commencé**
- Le chercheur peut importer sans soumettre immédiatement (ex. relire son code d'abord)

**Erreurs à gérer :**
- `413` — quota disque dépassé (afficher la consommation actuelle)
- `422` — extension non autorisée ou archive corrompue / trop volumineuse

---

### 4.2 Soumettre pour exécution distribuée

**Ce que voit l'utilisateur :** Bouton "Lancer l'exécution" sur la page de son projet.

```http
POST /api/tasks/{programme_id}/submit
Authorization: Bearer <access_token>
```

**Réponse 200 :**
```json
{
  "success": true,
  "message": "Programme soumis. La décomposition va démarrer.",
  "data": {
    "id": "a1b2c3d4...",
    "status": "en_decomposition",
    ...
  }
}
```

**Ce qui se passe côté cluster :**
1. Le statut passe à `en_decomposition`
2. L'agent maître détecte le changement, lit les sources, analyse les annotations
3. Il crée les sous-tâches atomiques (`TacheAtomique`) en base de données
4. Les workers récupèrent les tâches via `GET /api/cluster/tasks/next`
5. Le statut passe à `en_cours` dès la première tâche assignée

**Erreur 409 :** Le programme est déjà en cours ou terminé — afficher le statut actuel.

---

## 5. Parcours Chercheur — suivre et récupérer les résultats

### 5.1 Tableau de bord "Mes projets"

**Ce que voit l'utilisateur :** Liste de tous ses projets avec statut et barre de progression.

```http
GET /api/tasks/?page=1&per_page=20&status=en_cours
Authorization: Bearer <access_token>
```

**Réponse 200 :**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "a1b2c3d4...",
        "name": "Simulation Monte-Carlo turbulence",
        "status": "en_cours",
        "progress": {
          "total": 12,
          "done": 8,
          "failed": 0,
          "pending": 4,
          "percent": 66.7
        },
        ...
      }
    ],
    "total": 3,
    "page": 1,
    "per_page": 20,
    "pages": 1
  }
}
```

**Utilisation du champ `progress` :**
- Afficher une barre de progression : `percent` (0–100)
- `failed > 0` → avertissement orange (des retries sont en cours)
- `pending == 0 && failed > 0` → le programme va passer en `echec`

---

### 5.2 Suivi en temps réel d'un projet

**Ce que voit l'utilisateur :** Page de détail du projet avec avancement en temps réel.

```http
GET /api/tasks/{programme_id}
Authorization: Bearer <access_token>
```

**Stratégie de polling :**
```
Tant que status ∉ {termine, echec, annule} :
  Attendre 3 secondes
  GET /api/tasks/{id}
  Mettre à jour l'affichage
```

**Cycle de vie du statut :**
```
soumis → en_decomposition → en_cours → termine
                                     ↘ echec
                         (annulé par l'utilisateur) → annule
```

---

### 5.3 Voir les sous-tâches détaillées

**Ce que voit l'utilisateur :** Tableau "Détail de l'exécution" montrant quelle fonction
tourne sur quel nœud.

```http
GET /api/tasks/{programme_id}/tasks
Authorization: Bearer <access_token>
```

**Réponse (tableau de `TacheAtomique`) :**
```json
{
  "data": [
    {
      "id": "task-uuid-1",
      "function_name": "compute_turbulence_slice",
      "annotation_id": "split_bloc_0",
      "status": "terminee",
      "worker_node_uuid": "node-dell-01",
      "attempts": 1,
      "max_attempts": 3,
      "data_output": "{\"result\": [1.2, 3.4]}",
      "completed_at": "2024-09-15T14:35:10Z"
    },
    ...
  ]
}
```

---

### 5.4 Consulter les logs d'exécution

**Ce que voit l'utilisateur :** Zone de texte "Logs" dans la page de suivi.

```http
GET /api/tasks/{programme_id}/logs
Authorization: Bearer <access_token>
```

**Réponse :**
```json
{
  "data": {
    "logs": "[2024-09-15 14:30] Décomposition en 12 sous-tâches.\n[14:31] Worker node-dell-01 → tâche 1/12...\n[14:35] 8/12 terminées."
  }
}
```

Utile notamment quand le programme est en `echec` pour comprendre pourquoi.

---

### 5.5 Télécharger les résultats

**Ce que voit l'utilisateur :** Bouton "Télécharger les résultats" (actif uniquement si `status == "termine"`).

```http
GET /api/tasks/{programme_id}/result
Authorization: Bearer <access_token>
```

**Réponse :** Fichier ZIP en téléchargement direct (`Content-Disposition: attachment`).

**Implémentation frontend recommandée :**
```javascript
// Option 1 : ouvrir dans un nouvel onglet
window.open(`/api/tasks/${id}/result`, '_blank');

// Option 2 : fetch + Blob
const res = await fetch(`/api/tasks/${id}/result`, {
  headers: { Authorization: `Bearer ${token}` }
});
const blob = await res.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = `results_${id.slice(0, 8)}.zip`;
a.click();
```

**Erreur 409 :** Programme pas encore terminé — désactiver le bouton tant que `status != "termine"`.

---

### 5.6 Supprimer un projet

**Ce que voit l'utilisateur :** Bouton "Supprimer" avec confirmation.

```http
DELETE /api/tasks/{programme_id}
Authorization: Bearer <access_token>
```

Libère le stockage disque (sources + résultats supprimés). Le quota utilisateur est décrémenté.
Si le programme est en cours, les sous-tâches sont annulées avant suppression.

---

## 6. Parcours Gestionnaire — tableau de bord cluster

### 6.1 Vue synthétique du cluster

**Ce que voit l'utilisateur :** Cartes de statistiques en haut du tableau de bord.

```http
GET /api/nodes/stats
Authorization: Bearer <access_token>
```

**Réponse :**
```json
{
  "data": {
    "nodes": {
      "total": 6,
      "actifs": 4,
      "surcharges": 1,
      "en_panne": 0,
      "en_maintenance": 1
    },
    "tasks": {
      "en_cours": 7,
      "en_attente": 3
    }
  }
}
```

Afficher avec des indicateurs colorés :
- `actifs` → vert
- `surcharges` → orange
- `en_panne` → rouge
- `en_maintenance` → gris

**Rafraîchissement recommandé :** toutes les 5–10 secondes.

---

### 6.2 Liste des nœuds

**Ce que voit l'utilisateur :** Tableau des machines du cluster avec statut et métriques.

```http
GET /api/nodes/?page=1&per_page=50&status=actif
Authorization: Bearer <access_token>
```

**Affichage suggéré par nœud :**
- Nom d'hôte + IP
- Statut (badge coloré)
- CPU : barre de progression (`current_cpu_usage * 100`%)
- RAM : barre de progression (`current_ram_usage * 100`%)
- Tâches en cours : `current_tasks_count`
- Score d'élection : `current_score` (plus c'est haut, plus le nœud est préféré)
- Dernier heartbeat : `last_heartbeat_at` (alerte si > 5 s)

---

### 6.3 Détail d'un nœud

**Ce que voit l'utilisateur :** Page de détail d'un nœud avec historique d'utilisation.

```http
GET /api/nodes/{node_uuid}
Authorization: Bearer <access_token>
```

La réponse inclut `recent_heartbeats` (10 derniers) pour tracer des mini-graphes
d'utilisation CPU/RAM dans le temps.

Pour un historique plus long (graphe sur 30 min) :

```http
GET /api/nodes/{node_uuid}/heartbeats?limit=500
Authorization: Bearer <access_token>
```

---

### 6.4 Mettre un nœud en maintenance

**Ce que voit l'utilisateur :** Toggle "Maintenance" sur la fiche du nœud.

**Activer la maintenance** (avant une intervention physique) :
```http
PATCH /api/nodes/{node_uuid}/maintenance
Authorization: Bearer <access_token>
Content-Type: application/json

{ "enable": true }
```

Les tâches en cours sur ce nœud sont automatiquement migrées vers d'autres nœuds.

**Réactiver le nœud** (après l'intervention) :
```http
PATCH /api/nodes/{node_uuid}/maintenance
Content-Type: application/json

{ "enable": false }
```

---

### 6.5 Retirer un nœud du cluster

**Ce que voit l'utilisateur :** Bouton "Retirer du cluster" avec confirmation.

```http
DELETE /api/nodes/{node_uuid}
Authorization: Bearer <access_token>
```

Le nœud passe en `eteint`. Ses tâches en cours sont marquées `migree` pour réassignation.
Le nœud devra se réenregistrer pour réintégrer le cluster.

---

## 7. Parcours Gestionnaire — gérer les programmes

### 7.1 Vue globale de tous les programmes

**Ce que voit l'utilisateur :** Tableau "Tous les calculs en cours" (tous utilisateurs confondus).

```http
GET /api/programmes/?status=en_cours&page=1
Authorization: Bearer <access_token>
```

Même format de réponse que `GET /api/tasks/` mais sans filtre sur l'utilisateur.

---

### 7.2 Détail d'un programme (vue admin)

**Ce que voit l'utilisateur :** Page de détail enrichie avec la liste des nœuds participants.

```http
GET /api/programmes/{programme_id}
Authorization: Bearer <access_token>
```

**Champs supplémentaires :**
```json
{
  "data": {
    ...
    "worker_nodes": ["node-dell-01", "node-dell-02", "node-wyse-01"],
    "execution_log": "..."
  }
}
```

Permet d'identifier quels nœuds ont participé au calcul et de croiser avec leurs métriques.

---

### 7.3 Annuler un programme

**Ce que voit l'utilisateur :** Bouton "Forcer l'annulation" (rouge) avec confirmation.

```http
POST /api/programmes/{programme_id}/cancel
Authorization: Bearer <access_token>
```

**Réponse 200 :**
```json
{
  "success": true,
  "message": "Programme annulé. 5 sous-tâche(s) interrompue(s).",
  "data": { "status": "annule", ... }
}
```

**Erreur 409 :** Programme déjà dans un état terminal — désactiver le bouton si
`status ∈ {termine, echec, annule}`.

---

## 8. API Interne — agents C du cluster

> Ces routes sont appelées par le **code C des agents**, pas par le frontend.
> Elles nécessitent l'en-tête `X-Cluster-Key` à la place du JWT.

### 8.1 Enregistrement d'un nœud au démarrage

```http
POST /api/cluster/register
X-Cluster-Key: <clé_secrète>
Content-Type: application/json

{
  "uuid": "node-dell-01",
  "ip": "192.168.1.101",
  "hostname": "dell-optiplex-01",
  "role": "worker",
  "profile": {
    "cpu_cores": 2,
    "cpu_freq_mhz": 2933.0,
    "arch_cpu": "x86_64",
    "ram_total_mb": 4096,
    "ram_available_mb": 3800,
    "storage_total_gb": 160.0,
    "storage_available_gb": 80.5,
    "network_latency_ms": 1.2,
    "os_info": "Ubuntu 18.04.6 LTS"
  }
}
```

Si le nœud redémarre, cet appel remet son statut à `actif`.

---

### 8.2 Heartbeat périodique (T_HB = 2 s)

```http
POST /api/cluster/heartbeat
X-Cluster-Key: <clé_secrète>
Content-Type: application/json

{
  "uuid": "node-dell-01",
  "cpu_usage": 0.42,
  "ram_usage": 0.61,
  "tasks_in_progress": 2,
  "score": 0.73,
  "status": "actif",
  "ram_available_mb": 3200,
  "network_latency_ms": 1.5
}
```

**Réponse :** `{ "data": { "server_time": "2024-09-15T14:32:05Z" } }`

Le `server_time` peut être utilisé pour synchroniser l'horloge de l'agent.

---

### 8.3 Demande de tâche (polling worker)

```http
GET /api/cluster/tasks/next?node_uuid=node-dell-01
X-Cluster-Key: <clé_secrète>
```

**Réponse si tâche disponible :**
```json
{
  "data": {
    "task": {
      "id": "task-uuid-1",
      "function_name": "compute_turbulence_slice",
      "annotation_id": "split_bloc_0",
      "data_input": "{\"slice\": [0, 100]}",
      "status": "assignee"
    },
    "programme_source_path": "abc123/source/"
  }
}
```

**Réponse si aucune tâche :** `{ "data": null, "message": "Aucune tâche en attente." }`

Le worker doit alors attendre quelques secondes avant de repoll.

---

### 8.4 Retourner un résultat

**Format JSON (résultat petit) :**
```http
POST /api/cluster/tasks/{task_id}/result
X-Cluster-Key: <clé_secrète>
Content-Type: application/json

{
  "node_uuid": "node-dell-01",
  "output": { "matrix_row": [1.2, 3.4, 5.6] }
}
```

**Format multipart (résultat fichier binaire) :**
```http
POST /api/cluster/tasks/{task_id}/result
X-Cluster-Key: <clé_secrète>
Content-Type: multipart/form-data

node_uuid=node-dell-01
output={"result_summary": "ok"}
result_file=<fichier_binaire>
```

---

### 8.5 Signaler une erreur

```http
POST /api/cluster/tasks/{task_id}/error
X-Cluster-Key: <clé_secrète>
Content-Type: application/json

{
  "node_uuid": "node-dell-01",
  "reason": "MemoryError: impossible d'allouer 2 Go."
}
```

Si des tentatives restent (`attempts < max_attempts = 3`), la tâche repasse en `en_attente`
et sera assignée à un autre worker. Sinon elle passe en `echouee`.

---

## 9. Stratégie de gestion des tokens JWT

### Cycle de vie

```
Connexion
  → access_token (15 min)   stocké en mémoire / sessionStorage
  → refresh_token (30 jours) stocké en cookie httpOnly

Requête API :
  → Si 401 "Token expiré"
      → POST /api/auth/refresh (avec refresh_token)
      → Nouveau access_token → retry la requête originale
      → Si refresh aussi expiré → logout + redirection connexion
```

### Implémentation avec intercepteur (exemple Axios)

```javascript
// Intercepteur de réponse
axios.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      try {
        const { data } = await axios.post('/api/auth/refresh', null, {
          headers: { Authorization: `Bearer ${getRefreshToken()}` }
        });
        setAccessToken(data.data.access_token);
        original.headers.Authorization = `Bearer ${data.data.access_token}`;
        return axios(original);
      } catch {
        logout();
      }
    }
    return Promise.reject(error);
  }
);
```

### Stockage recommandé

| Token          | Stockage recommandé        | Raison                              |
|----------------|----------------------------|-------------------------------------|
| `access_token` | Mémoire JS (variable)      | Courte durée, pas besoin de persist |
| `refresh_token`| Cookie `httpOnly; Secure`  | Protégé contre XSS                  |

---

## 10. Codes d'erreur et gestion des cas limites

| Code | Signification                                       | Action frontend                                    |
|------|-----------------------------------------------------|----------------------------------------------------|
| 400  | Données invalides                                   | Afficher `error` près du champ concerné            |
| 401  | Token manquant/expiré                               | Tenter refresh, sinon logout                       |
| 403  | Rôle insuffisant                                    | Masquer les actions non autorisées dans l'UI       |
| 404  | Ressource introuvable                               | Afficher message "non trouvé", rediriger si besoin |
| 409  | Conflit d'état (déjà en cours, déjà en maintenance) | Rafraîchir les données et désactiver le bouton     |
| 413  | Quota disque dépassé                                | Afficher consommation actuelle, proposer suppression|
| 422  | Fichier non valide / zip bomb                       | Expliquer les formats et tailles acceptés          |
| 500  | Erreur serveur interne                              | Message générique + log côté client               |
| 503  | Nœud indisponible                                   | Réessayer plus tard (worker polling uniquement)    |

### Règles d'affichage des boutons selon le statut

| Statut programme  | "Soumettre" | "Télécharger" | "Annuler" | "Supprimer" |
|-------------------|-------------|---------------|-----------|-------------|
| `soumis`          | ✅ actif    | ❌ désactivé  | ❌        | ✅          |
| `en_decomposition`| ❌          | ❌            | ✅        | ❌          |
| `en_cours`        | ❌          | ❌            | ✅        | ❌          |
| `termine`         | ❌          | ✅ actif      | ❌        | ✅          |
| `echec`           | ✅ retry    | ❌            | ❌        | ✅          |
| `annule`          | ❌          | ❌            | ❌        | ✅          |

---

## Résumé des endpoints

| Méthode | Endpoint                                | Rôle requis       | Usage                            |
|---------|-----------------------------------------|-------------------|----------------------------------|
| POST    | `/api/auth/register`                    | —                 | Inscription                      |
| POST    | `/api/auth/login`                       | —                 | Connexion                        |
| POST    | `/api/auth/refresh`                     | refresh token     | Renouveler l'access token        |
| POST    | `/api/auth/logout`                      | any               | Déconnexion                      |
| GET     | `/api/auth/me`                          | any               | Profil courant                   |
| GET     | `/api/tasks/`                           | any               | Mes projets                      |
| POST    | `/api/tasks/import`                     | any               | Uploader un projet               |
| GET     | `/api/tasks/{id}`                       | any               | Détail projet + progression      |
| POST    | `/api/tasks/{id}/submit`                | any               | Lancer l'exécution               |
| DELETE  | `/api/tasks/{id}`                       | any               | Supprimer un projet              |
| GET     | `/api/tasks/{id}/result`                | any               | Télécharger les résultats        |
| GET     | `/api/tasks/{id}/logs`                  | any               | Logs d'exécution                 |
| GET     | `/api/tasks/{id}/tasks`                 | any               | Sous-tâches atomiques            |
| GET     | `/api/nodes/stats`                      | gestionnaire      | Stats globales cluster           |
| GET     | `/api/nodes/`                           | gestionnaire      | Liste nœuds                      |
| GET     | `/api/nodes/{uuid}`                     | gestionnaire      | Détail nœud                      |
| DELETE  | `/api/nodes/{uuid}`                     | gestionnaire      | Retirer un nœud                  |
| GET     | `/api/nodes/{uuid}/tasks`               | gestionnaire      | Tâches d'un nœud                 |
| GET     | `/api/nodes/{uuid}/heartbeats`          | gestionnaire      | Historique heartbeats            |
| PATCH   | `/api/nodes/{uuid}/maintenance`         | gestionnaire      | Mode maintenance                 |
| GET     | `/api/programmes/`                      | gestionnaire      | Tous les programmes              |
| GET     | `/api/programmes/{id}`                  | gestionnaire      | Détail programme (admin)         |
| POST    | `/api/programmes/{id}/cancel`           | gestionnaire      | Annuler un programme             |
| POST    | `/api/cluster/register`                 | X-Cluster-Key     | Enregistrer un nœud              |
| POST    | `/api/cluster/heartbeat`                | X-Cluster-Key     | Heartbeat nœud                   |
| GET     | `/api/cluster/tasks/next`               | X-Cluster-Key     | Prochaine tâche (worker)         |
| POST    | `/api/cluster/tasks/{id}/result`        | X-Cluster-Key     | Résultat de tâche                |
| POST    | `/api/cluster/tasks/{id}/error`         | X-Cluster-Key     | Erreur de tâche                  |
| GET     | `/api/docs/`                            | —                 | Swagger UI                       |
| GET     | `/api/openapi.json`                     | —                 | Spec OpenAPI brute               |
