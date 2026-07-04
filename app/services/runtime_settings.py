"""
Overrides de configuration modifiables à chaud depuis l'UI (gestionnaire),
sans toucher au .env ni redémarrer le process Flask.

Aujourd'hui limité à l'adresse du Receptionist (RECEPTIONIST_IP /
RECEPTIONIST_HTTP_PORT) : c'est la seule valeur qu'un gestionnaire a besoin
d'ajuster en cours de route, typiquement quand la machine qui héberite le
Receptionist change d'IP (DHCP) pendant une session de test.

Persisté dans instance/runtime_settings.json (créé au premier appel de
set_receptionist_config) pour survivre à un redémarrage du backend — sans ce
fichier, on retombe sur RECEPTIONIST_IP/RECEPTIONIST_HTTP_PORT du .env.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

from flask import current_app

logger = logging.getLogger(__name__)

_lock = Lock()


def _settings_path() -> Path:
    return Path(current_app.instance_path) / "runtime_settings.json"


def _read_overrides() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("runtime_settings.json illisible, ignoré : %s", exc)
        return {}


def get_receptionist_config() -> dict:
    """Retourne {ip, port}, l'override persisté ayant priorité sur le .env."""
    overrides = _read_overrides().get("receptionist", {})
    return {
        "ip": overrides.get("ip") or current_app.config.get("RECEPTIONIST_IP"),
        "port": overrides.get("port") or current_app.config["RECEPTIONIST_HTTP_PORT"],
    }


def set_receptionist_config(ip: str, port: int) -> dict:
    """Persiste le nouvel override et l'applique immédiatement à cette instance."""
    with _lock:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read_overrides()
        data["receptionist"] = {"ip": ip, "port": port}
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # Applique tout de suite pour ce process, sans attendre un redémarrage.
    current_app.config["RECEPTIONIST_IP"] = ip
    current_app.config["RECEPTIONIST_HTTP_PORT"] = port

    return {"ip": ip, "port": port}
