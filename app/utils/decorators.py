"""Décorateurs d'autorisation pour les routes Flask."""
from functools import wraps

from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.models.user import User, UserRole
from app.utils.responses import forbidden, error


def require_role(*roles: str):
    """
    Décorateur qui vérifie que l'utilisateur courant possède l'un des rôles demandés.
    Doit être utilisé après @jwt_required().
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if user is None or not user.is_active:
                return error("Compte utilisateur introuvable ou désactivé.", status=401)
            if user.role not in roles:
                return forbidden(
                    f"Accès réservé aux rôles : {', '.join(roles)}."
                )
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def gestionnaire_required(fn):
    """Alias : route accessible uniquement aux gestionnaires de cluster."""
    return require_role(UserRole.GESTIONNAIRE.value)(fn)


def chercheur_required(fn):
    """Alias : route accessible aux chercheurs et étudiants."""
    return require_role(UserRole.CHERCHEUR.value, UserRole.ETUDIANT.value)(fn)


def cluster_internal(fn):
    """
    Vérifie la clé API interne du cluster pour les appels noeud→maître.
    Lit l'en-tête X-Cluster-Key.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        from flask import request, current_app
        key = request.headers.get("X-Cluster-Key", "")
        if key != current_app.config["CLUSTER_INTERNAL_KEY"]:
            return forbidden("Clé interne cluster invalide.")
        return fn(*args, **kwargs)
    return wrapper
