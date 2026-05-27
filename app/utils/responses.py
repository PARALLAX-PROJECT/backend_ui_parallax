"""Helpers pour des réponses JSON cohérentes dans toute l'API."""
from flask import jsonify


def success(data=None, message: str = "OK", status: int = 200):
    body = {"success": True, "message": message}
    if data is not None:
        body["data"] = data
    return jsonify(body), status


def created(data=None, message: str = "Créé avec succès"):
    return success(data=data, message=message, status=201)


def error(message: str, status: int = 400, details=None):
    body = {"success": False, "error": message}
    if details is not None:
        body["details"] = details
    return jsonify(body), status


def not_found(resource: str = "Ressource"):
    return error(f"{resource} introuvable.", status=404)


def forbidden(message: str = "Accès refusé."):
    return error(message, status=403)


def conflict(message: str):
    return error(message, status=409)


def server_error(message: str = "Erreur interne du serveur."):
    return error(message, status=500)
