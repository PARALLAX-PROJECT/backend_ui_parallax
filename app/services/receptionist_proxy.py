"""
Proxy HTTP vers le serveur Receptionist (port 9010 par défaut).

Le Receptionist expose une vue live du cluster obtenue directement depuis
la couche de gossip du contrôleur — rien n'est persisté côté Flask :

    GET /nodes              -> liste de tous les noeuds connus du contrôleur
    GET /node-logs/<uuid>   -> contenu du buffer de log d'un noeud
    GET /logs               -> liste des logs de programmes terminés (relayés par le maître)
    GET /logs/<name>        -> contenu d'un log de programme

Voir Receptionnist/reception.c (handle_get_nodes, handle_get_node_logs, handle_get_logs).
Ces routes répondent parfois HTTP 200 avec un corps `{"error": "..."}`
lorsque le Receptionist n'est pas connecté au contrôleur — on détecte
ce cas explicitement pour ne pas le confondre avec une vraie liste vide.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from app.services.runtime_settings import get_receptionist_config

logger = logging.getLogger(__name__)


class ReceptionistProxyError(Exception):
    """Erreur de communication avec le Receptionist ou le contrôleur en amont."""


class NodeLogNotFoundError(Exception):
    """Aucun log disponible pour ce noeud (pas encore de log ou uuid inconnu)."""


class ClusterLogNotFoundError(Exception):
    """Ce fichier de log de programme n'existe pas sur le Receptionist."""


def _receptionist_base_url() -> str:
    cfg = get_receptionist_config()
    ip = cfg["ip"]
    if not ip:
        raise ReceptionistProxyError(
            "Adresse du Receptionist non configurée — renseignez-la depuis "
            "l'interface (barre latérale) ou RECEPTIONIST_IP dans .env."
        )
    return f"http://{ip}:{cfg['port']}"


def _get(path: str) -> tuple[int, bytes]:
    url = _receptionist_base_url() + path
    timeout = current_app.config["RECEPTIONIST_TIMEOUT_S"]
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReceptionistProxyError(f"Receptionist injoignable ({url}) : {exc}") from exc


def _raise_if_error_payload(text: str) -> None:
    """Le Receptionist renvoie parfois `{"error": "..."}` avec un code 200."""
    stripped = text.strip()
    if stripped.startswith("{") and '"error"' in stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict) and "error" in payload:
            raise ReceptionistProxyError(payload["error"])


def fetch_live_nodes() -> list[dict]:
    """
    Récupère la liste live des noeuds connus du contrôleur, via le Receptionist.
    Lève ReceptionistProxyError si le Receptionist ou le contrôleur est injoignable.
    """
    status, body = _get("/nodes")
    text = body.decode("utf-8", errors="replace")

    if status != 200:
        raise ReceptionistProxyError(text or f"Erreur Receptionist (HTTP {status}).")

    _raise_if_error_payload(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReceptionistProxyError(f"Réponse invalide du Receptionist : {exc}") from exc

    if not isinstance(data, list):
        raise ReceptionistProxyError("Format de réponse /nodes inattendu.")
    return data


def fetch_node_log(node_uuid: str) -> str:
    """
    Récupère le contenu du log d'un noeud via le Receptionist.
    Lève NodeLogNotFoundError si aucun log n'est encore disponible pour ce noeud,
    ReceptionistProxyError en cas d'échec de communication avec le cluster.
    """
    safe_uuid = urllib.parse.quote(node_uuid, safe="")
    status, body = _get(f"/node-logs/{safe_uuid}")
    text = body.decode("utf-8", errors="replace")

    if status == 404:
        raise NodeLogNotFoundError(text or "Aucun log disponible pour ce noeud.")
    if status != 200:
        raise ReceptionistProxyError(text or f"Erreur Receptionist (HTTP {status}).")

    _raise_if_error_payload(text)
    return text


def submit_program(code: bytes) -> str:
    """
    Envoie le code source d'un programme au Receptionist pour exécution.

    Le Receptionist traite toute requête HTTP sur un chemin non reconnu (/nodes,
    /logs, /node-logs/... étant réservés) comme une soumission de code : il stocke
    le corps de la requête tel quel et le relaie au maître dès que celui-ci est
    connu (voir forward_code_to_master / code_submission_listener_thread côté C).

    Ne lève PAS d'erreur si le maître n'est pas encore joignable : le Receptionist
    met la soumission en attente et la relaie dès qu'un maître se fait connaître.
    Lève ReceptionistProxyError uniquement si le Receptionist lui-même est injoignable.

    Utilise un socket brut plutôt que urllib : le serveur HTTP du Receptionist
    (Receptionnist/reception.c) lit la requête en un seul appel `read()` et
    cherche `\r\n\r\n` dans ce même buffer. urllib envoie parfois les en-têtes
    et le corps en deux paquets TCP distincts, ce que ce parseur minimal ne
    gère pas (le corps arrive après le `read()`, la soumission est silencieusement
    ignorée bien que le serveur réponde 200 OK). Un unique `sendall()` avec la
    requête complète (en-têtes + corps) évite ce problème.

    Retourne le corps de la réponse HTTP (normalement "OK").
    """
    cfg = get_receptionist_config()
    ip = cfg["ip"]
    if not ip:
        raise ReceptionistProxyError(
            "Adresse du Receptionist non configurée — renseignez-la depuis "
            "l'interface (barre latérale) ou RECEPTIONIST_IP dans .env."
        )
    port = cfg["port"]
    timeout = current_app.config["RECEPTIONIST_TIMEOUT_S"]

    request = (
        b"POST / HTTP/1.1\r\n"
        b"Host: " + f"{ip}:{port}".encode("ascii") + b"\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(code)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n" + code
    )

    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.sendall(request)
            sock.settimeout(timeout)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        raise ReceptionistProxyError(
            f"Receptionist injoignable ({ip}:{port}) : {exc}"
        ) from exc

    raw = b"".join(chunks)
    body = raw.split(b"\r\n\r\n", 1)[-1]
    return body.decode("utf-8", errors="replace")


def fetch_cluster_logs() -> list[dict]:
    """
    Liste les logs de programmes terminés que le Receptionist a reçus du maître
    (relayés via le contrôleur — voir handle_get_logs côté C, cas sans suffixe).
    Lève ReceptionistProxyError si le Receptionist est injoignable.
    """
    status, body = _get("/logs")
    text = body.decode("utf-8", errors="replace")

    if status != 200:
        raise ReceptionistProxyError(text or f"Erreur Receptionist (HTTP {status}).")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReceptionistProxyError(f"Réponse invalide du Receptionist : {exc}") from exc

    if not isinstance(data, list):
        raise ReceptionistProxyError("Format de réponse /logs inattendu.")
    return data


def fetch_cluster_log_content(name: str) -> str:
    """
    Récupère le contenu d'un log de programme par son nom de fichier
    (tel que renvoyé par fetch_cluster_logs).
    Lève ClusterLogNotFoundError si le fichier n'existe pas,
    ReceptionistProxyError en cas d'échec de communication avec le Receptionist.
    """
    safe_name = urllib.parse.quote(name, safe="")
    status, body = _get(f"/logs/{safe_name}")
    text = body.decode("utf-8", errors="replace")

    if status == 404:
        raise ClusterLogNotFoundError(text or "Log introuvable.")
    if status != 200:
        raise ReceptionistProxyError(text or f"Erreur Receptionist (HTTP {status}).")

    return text
