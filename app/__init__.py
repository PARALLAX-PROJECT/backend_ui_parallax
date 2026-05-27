"""
PARALLAX Backend — Flask Application Factory
"""
import logging
from pathlib import Path

from flasgger import Swagger
from flask import Flask, jsonify
from flask_jwt_extended import JWTManager

from app.config import get_config
from app.extensions import cors, db, jwt, migrate
from app.swagger_spec import SWAGGER_CONFIG, SWAGGER_TEMPLATE


def create_app(config_class=None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    # ── Configuration ──────────────────────────────────────────────
    cfg = config_class or get_config()
    app.config.from_object(cfg)

    # S'assurer que le répertoire de stockage existe
    storage_root: Path = app.config["STORAGE_ROOT"]
    storage_root.mkdir(parents=True, exist_ok=True)

    # ── Extensions ─────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )

    # ── JWT callbacks ──────────────────────────────────────────────
    _register_jwt_callbacks(app, jwt)

    # ── Blueprints ─────────────────────────────────────────────────
    from app.api import register_blueprints
    register_blueprints(app)

    # ── Swagger UI (/api/docs/) ────────────────────────────────────
    Swagger(app, template=SWAGGER_TEMPLATE, config=SWAGGER_CONFIG)

    # ── Gestion globale des erreurs ────────────────────────────────
    _register_error_handlers(app)

    # ── Démarrage du moniteur de noeuds ────────────────────────────
    from app.services.node_monitor import monitor
    monitor.init_app(app)
    with app.app_context():
        db.create_all()
    monitor.start()

    # ── Logging ────────────────────────────────────────────────────
    if not app.debug:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.DEBUG)

    app.logger.info("PARALLAX backend démarré (env=%s)", app.config.get("ENV", "?"))
    return app


def _register_jwt_callbacks(app: Flask, jwt_manager: JWTManager) -> None:
    from app.models.user import TokenBlocklist

    @jwt_manager.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        jti = jwt_payload["jti"]
        return TokenBlocklist.query.filter_by(jti=jti).first() is not None

    @jwt_manager.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"success": False, "error": "Token expiré."}), 401

    @jwt_manager.invalid_token_loader
    def invalid_token_callback(reason):
        return jsonify({"success": False, "error": f"Token invalide : {reason}"}), 422

    @jwt_manager.unauthorized_loader
    def missing_token_callback(reason):
        return jsonify({"success": False, "error": "Authentification requise."}), 401

    @jwt_manager.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({"success": False, "error": "Token révoqué."}), 401


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "error": "Route introuvable."}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"success": False, "error": "Méthode HTTP non autorisée."}), 405

    @app.errorhandler(413)
    def request_too_large(e):
        return jsonify({
            "success": False,
            "error": "Fichier trop volumineux. Limite : 100 Mo.",
        }), 413

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.exception("Erreur interne")
        return jsonify({"success": False, "error": "Erreur interne du serveur."}), 500
