# Intégration cluster réelle — changements apportés au backend

Ce document résume les endpoints et la logique ajoutés au backend Flask
(`backend_ui_parallax/`) pour le connecter au cluster PARALLAX réel (Controller +
Receptionist + agent maître en C), à la place des chemins qui ne faisaient que
lire/écrire en base sans jamais parler aux agents C.

Contexte : avant ces changements, `POST /api/tasks/<id>/submit` envoyait le
programme via un protocole TCP maison (`app/services/dispatch.py`,
`DISCOVER_MASTER`/`PROGRAM`) qui ne correspond à aucun type de message reconnu
par le code C du cluster — la soumission échouait donc toujours contre le
cluster réel et ne fonctionnait qu'en mode « dispatch ignoré ». Le vrai point
d'entrée du cluster est le serveur HTTP du **Receptionist** (port 9010, voir
`Receptionnist/reception.c`), qui expose une vue live du cluster obtenue par
la couche de gossip du contrôleur, et qui accepte les soumissions de code brut.

---

## 1. Nouveaux endpoints

### `GET /api/nodes/cluster-logs`
Liste les logs de programmes terminés que le Receptionist a reçus du maître
(relayés via le contrôleur, message `PROG_LOG`). Proxy direct de
`GET /logs` sur le Receptionist. Gestionnaire uniquement.

### `GET /api/nodes/cluster-logs/<log_name>`
Contenu d'un log de programme par nom de fichier. Proxy de `GET /logs/<name>`
sur le Receptionist. 404 si le fichier n'existe pas. Gestionnaire uniquement.

*(Distinct de `GET /api/tasks/<id>/logs`, qui lit `Programme.execution_log`
en base — voir section 3.)*

### `POST /api/cluster/programme-result`
Nouveau point d'entrée interne (`X-Cluster-Key` requis, comme le reste de
`/api/cluster/*`). Appelé par le Receptionist lui-même (pas par un utilisateur)
une fois qu'il a reçu le `PROG_LOG` d'un programme et identifié, via sa table
de callbacks en mémoire, quel backend l'a soumis.

Body attendu :
```json
{ "programme_id": "<uuid>", "status": "termine", "log": "<contenu du log>" }
```
Effet : `Programme.execution_log = log`, puis `mark_done()` (ou `mark_failed()`
si `status == "echec"`, mais ce cas n'est pas encore émis côté C — voir
Limitations).

Fichier : `app/api/cluster.py`.

---

## 2. `app/services/receptionist_proxy.py` — logique ajoutée/corrigée

| Fonction | Rôle |
|---|---|
| `fetch_cluster_logs()` | `GET /logs` sur le Receptionist → liste `[{name, size}]` |
| `fetch_cluster_log_content(name)` | `GET /logs/<name>` sur le Receptionist → contenu texte |
| `submit_program(code)` | `POST /` sur le Receptionist avec le code source brut en corps de requête |
| `ClusterLogNotFoundError` | levée quand un nom de log n'existe pas (404 côté Receptionist) |

**Bug corrigé dans `submit_program`** : la première version utilisait
`urllib.request`, qui peut envoyer les en-têtes HTTP et le corps en deux
paquets TCP séparés. Le serveur HTTP du Receptionist (`reception.c`) lit la
requête en un seul appel `read()` et cherche `\r\n\r\n` dans ce même buffer :
si le corps arrive dans un second paquet, il est silencieusement ignoré alors
que le serveur répond quand même `200 OK`. `submit_program` construit
désormais la requête complète (en-têtes + corps) et l'envoie en un seul
`socket.sendall()`, comme le fait `curl`.

---

## 3. `app/api/tasks.py` — soumission réécrite

`POST /api/tasks/<id>/submit` (fonction `submit_programme`) ne fait plus de
dispatch TCP. Nouvelle séquence :

1. Lit le fichier source principal du projet (réutilise `_read_source_file`
   de `app/services/dispatch.py`).
2. `_with_prog_name_marker(code, prog.id, suffix)` — préfixe le code d'un
   commentaire `// __parallax_prog_name__ = "<uuid_programme>"` (C/C++
   uniquement). Le Receptionist s'en sert pour nommer le fichier de log
   (`extract_prog_name` dans `reception.c`). **Important** : on utilise
   l'UUID du programme plutôt que le nom choisi par l'utilisateur, pour
   éviter que deux utilisateurs avec un programme du même nom n'écrasent le
   log l'un de l'autre (le Receptionist ne connaît que ce nom, pas
   l'utilisateur).
3. `_with_callback_markers(code, suffix)` — si `BACKEND_CALLBACK_HOST` est
   configuré, préfixe deux marqueurs supplémentaires
   (`__parallax_callback_host__`, `__parallax_callback_port__`) que le
   Receptionist extrait pour savoir où renvoyer le résultat (voir section 4).
4. `submit_program(code)` → POST brut vers le Receptionist.
5. Si succès : `prog.mark_submitted()` (`status → en_decomposition`).
   Si le Receptionist est injoignable : comportement dev-friendly identique à
   avant (soumission marquée quand même si `DISPATCH_REQUIRED=false`, sinon
   503).

`_resolve_master_ip()`, `discover_master_ip()` et `send_programme_to_master()`
ne sont plus utilisés par ce chemin (protocole TCP maison incompatible avec
le code C réel — conservés dans `dispatch.py` mais plus appelés depuis
`tasks.py`).

---

## 4. Boucle de statut fermée (push depuis le Receptionist)

Avant : une fois le programme envoyé au maître, le backend n'avait aucun
moyen de savoir qu'il était terminé (`status` restait bloqué à
`en_decomposition`).

Maintenant : le Receptionist tient une petite table en mémoire
(`callback_registry_add`/`callback_registry_take` dans `reception.c`) qui
associe `nom_de_programme → (host, port)` au moment de la soumission (extraits
des marqueurs `__parallax_callback_*`). Quand le log d'exécution du programme
arrive (`log_receiver_thread`), le Receptionist consulte cette table et, si
une entrée correspond, ouvre lui-même une connexion HTTP vers
`POST /api/cluster/programme-result` sur ce backend avec le log et le statut.

Ceci ferme la boucle sans que le backend ait besoin de sonder le cluster en
boucle.

**Limitation connue** : côté C, l'agent maître (`Execution_Master/utils/master_thread.c`)
n'envoie un `PROG_LOG` qu'après une compilation **et** une exécution réussies.
Un échec de compilation ne produit aucun message — le programme reste
`en_decomposition` indéfiniment dans ce cas. `status: "echec"` est accepté par
`/api/cluster/programme-result` pour anticiper un futur signal d'échec côté C,
mais rien ne l'émet encore aujourd'hui.

---

## 5. Nouvelle configuration

`app/config.py` + `.env` / `.env.example` :

```
BACKEND_CALLBACK_HOST=127.0.0.1   # IP à laquelle CE backend est joignable depuis le Receptionist
BACKEND_CALLBACK_PORT=5000
```

Si `BACKEND_CALLBACK_HOST` n'est pas défini, `_with_callback_markers` ne fait
rien : la soumission fonctionne toujours (log écrit sur le Receptionist,
consultable via `GET /api/nodes/cluster-logs`), mais sans mise à jour
automatique du statut.

---

## 6. Contrepartie côté C (hors backend, pour référence)

Ces changements Flask dépendent d'ajouts symétriques dans
`Receptionnist/reception.c` (registre de callbacks, `extract_marker`,
`send_result_callback`, hook dans `log_receiver_thread`) et d'une constante
`BACKEND_CLUSTER_KEY` qui **doit rester synchronisée** avec
`CLUSTER_INTERNAL_KEY` dans `.env`.
