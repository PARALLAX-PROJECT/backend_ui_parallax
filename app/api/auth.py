"""
Blueprint /api/auth

Routes :
  POST /register   – Création de compte
  POST /login      – Authentification, retourne access + refresh token
  POST /refresh    – Renouvelle l'access token via le refresh token
  POST /logout     – Révoque le token courant (blacklist)
  GET  /me         – Profil de l'utilisateur connecté
"""
import re
from datetime import datetime, timezone

from flask import Blueprint, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)

from app.extensions import db
from app.models.user import TokenBlocklist, User, UserRole
from app.utils.responses import conflict, created, error, forbidden, not_found, success

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,80}$")


# ──────────────────────────────────────────────
# POST /api/auth/register
# ──────────────────────────────────────────────
@bp.route("/register", methods=["POST"])
def register():
    """
    Créer un nouveau compte utilisateur.
    ---
    tags:
      - Authentification
    summary: Inscription
    description: |
      Crée un compte chercheur ou gestionnaire. Le nom d'utilisateur et l'e-mail
      doivent être uniques dans le système.
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/UserRegisterRequest'
          example:
            username: alice_dupont
            email: alice@enspy.cm
            password: s3cr3t!Pass
            role: chercheur
    responses:
      201:
        description: Compte créé avec succès.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/UserResponse'
      400:
        description: Données invalides (format username, email, mot de passe trop court, rôle inconnu).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      409:
        description: Nom d'utilisateur ou e-mail déjà utilisé.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = (data.get("role") or UserRole.CHERCHEUR.value).strip().lower()

    # Validation
    if not username or not _USERNAME_RE.match(username):
        return error(
            "Nom d'utilisateur invalide (3-80 caractères alphanumériques, _, ., -).",
        )
    if not email or not _EMAIL_RE.match(email):
        return error("Adresse e-mail invalide.")
    if len(password) < 8:
        return error("Le mot de passe doit comporter au moins 8 caractères.")
    allowed_roles = (UserRole.CHERCHEUR.value, UserRole.ETUDIANT.value, UserRole.GESTIONNAIRE.value)
    if role not in allowed_roles:
        return error(f"Rôle invalide. Valeurs acceptées : chercheur, etudiant, gestionnaire.")

    if User.query.filter_by(username=username).first():
        return conflict(f"Le nom d'utilisateur « {username} » est déjà pris.")
    if User.query.filter_by(email=email).first():
        return conflict(f"L'adresse e-mail est déjà utilisée.")

    user = User(username=username, email=email, role=role)
    try:
        user.set_password(password)
    except ValueError as exc:
        return error(str(exc))

    db.session.add(user)
    db.session.commit()

    return created(
        data=user.to_dict(),
        message="Compte créé avec succès.",
    )


# ──────────────────────────────────────────────
# POST /api/auth/login
# ──────────────────────────────────────────────
@bp.route("/login", methods=["POST"])
def login():
    """
    Authentifier un utilisateur et obtenir des tokens JWT.
    ---
    tags:
      - Authentification
    summary: Connexion
    description: |
      Retourne un **access token** (durée courte, ~15 min) et un **refresh token**
      (durée longue, ~30 jours). L'identifiant peut être le nom d'utilisateur
      ou l'adresse e-mail.

      Le frontend doit stocker les deux tokens (ex. : `localStorage` ou cookie httpOnly)
      et inclure l'access token dans toutes les requêtes suivantes :
      ```
      Authorization: Bearer <access_token>
      ```
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/UserLoginRequest'
          examples:
            par_username:
              summary: Connexion par nom d'utilisateur
              value:
                username: alice_dupont
                password: s3cr3t!Pass
            par_email:
              summary: Connexion par e-mail
              value:
                email: alice@enspy.cm
                password: s3cr3t!Pass
    responses:
      200:
        description: Connexion réussie. Tokens JWT retournés.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/AuthTokensResponse'
      400:
        description: Identifiant ou mot de passe manquant.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Identifiants incorrects.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Compte désactivé.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    data = request.get_json(silent=True) or {}
    identifier = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""

    if not identifier or not password:
        return error("Identifiant et mot de passe requis.")

    user = User.query.filter(
        (User.username == identifier) | (User.email == identifier.lower())
    ).first()

    if user is None or not user.check_password(password):
        return error("Identifiants incorrects.", status=401)
    if not user.is_active:
        return forbidden("Ce compte est désactivé.")

    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)

    return success(
        data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user.to_dict(),
        },
        message="Connexion réussie.",
    )


# ──────────────────────────────────────────────
# POST /api/auth/refresh
# ──────────────────────────────────────────────
@bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """
    Renouveler l'access token via le refresh token.
    ---
    tags:
      - Authentification
    summary: Rafraîchissement du token
    description: |
      L'access token ayant une durée de vie courte, le frontend doit appeler
      cet endpoint dès qu'il reçoit un `401` sur une requête protégée.

      **Important :** passer le **refresh token** (pas l'access token) dans
      l'en-tête `Authorization`.
    security:
      - BearerAuth: []
    responses:
      200:
        description: Nouvel access token émis.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/RefreshTokenResponse'
      401:
        description: Refresh token invalide, expiré ou révoqué.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if user is None or not user.is_active:
        return error("Utilisateur introuvable ou désactivé.", status=401)

    new_access = create_access_token(identity=user_id)
    return success(data={"access_token": new_access}, message="Token renouvelé.")


# ──────────────────────────────────────────────
# POST /api/auth/logout
# ──────────────────────────────────────────────
@bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """
    Révoquer le token courant (déconnexion).
    ---
    tags:
      - Authentification
    summary: Déconnexion
    description: |
      Ajoute le JTI (JWT ID) de l'access token courant à la liste noire en base de données.
      Toute requête ultérieure avec ce token sera rejetée avec `401`.

      Le frontend doit également supprimer les tokens stockés localement
      (localStorage, cookie, etc.).
    security:
      - BearerAuth: []
    responses:
      200:
        description: Déconnexion réussie.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiSuccessResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    jti = get_jwt()["jti"]
    db.session.add(TokenBlocklist(jti=jti))
    db.session.commit()
    return success(message="Déconnexion réussie.")


# ──────────────────────────────────────────────
# GET /api/auth/me
# ──────────────────────────────────────────────
@bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    """
    Profil de l'utilisateur actuellement authentifié.
    ---
    tags:
      - Authentification
    summary: Mon profil
    description: |
      Retourne le profil complet de l'utilisateur identifié par l'access token.
      Utile au démarrage de l'application pour vérifier la session et récupérer
      le rôle de l'utilisateur (chercheur ou gestionnaire) afin d'afficher
      la bonne interface.
    security:
      - BearerAuth: []
    responses:
      200:
        description: Profil utilisateur.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/UserResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Utilisateur introuvable (compte supprimé).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if user is None:
        return not_found("Utilisateur")
    return success(data=user.to_dict())
