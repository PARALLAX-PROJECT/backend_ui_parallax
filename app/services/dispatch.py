"""
Service de dispatch TCP vers les agents C du cluster PARALLAX.

Implémente le protocole binaire défini par les structures C :

    typedef struct {
        long      mq_type;        // 8 octets (long 64-bit Linux)
        char      type[64];       // type du message (ex. "DISCOVER_MASTER")
        char      recv_type[64];  // type du destinataire (ex. "CONTROLLER")
        uint64_t  size;           // longueur du champ data[] en octets
        char      data[];         // payload (flexible array member)
    } message_t;

    typedef struct {
        char     program_name[PROG_NAME_MAX];  // nom du programme (null-padded)
        uint32_t code_size;                    // longueur effective du code
        char     code[PROG_CODE_MAX];          // code source (null-padded)
    } program_message_t;

Flux de soumission :
  1. Backend → Contrôleur (CONTROLLER_DISPATCH_PORT)
       send: message_t { type="DISCOVER_MASTER", recv_type="CONTROLLER", data="" }
       recv: message_t { type="MASTER_IP",       data="<ip_du_maitre>" }

  2. Backend → Maître (MASTER_DISPATCH_PORT)
       send: message_t { type="PROGRAM", recv_type="MASTER",
                         data=program_message_t{ program_name, code_size, code } }
       recv: message_t { type="ACK" }  (optionnel — timeout silencieux)
"""
from __future__ import annotations

import logging
import socket
import struct
from pathlib import Path

from flask import current_app

logger = logging.getLogger(__name__)

# ─── Layout binaire de message_t ────────────────────────────────────────────
# long (8) + char[64] + char[64] + uint64_t (8) = 144 octets
_MSG_FMT = "=l64s64sQ"          # '=' = native byte order sans alignement forcé
MSG_HEADER_SIZE: int = struct.calcsize(_MSG_FMT)  # 144

# Types de messages reconnus
MSG_DISCOVER_MASTER = "DISCOVER_MASTER"
MSG_MASTER_IP       = "MASTER_IP"
MSG_PROGRAM         = "PROGRAM"
MSG_ACK             = "ACK"
MSG_ERROR           = "ERROR"

RECV_CONTROLLER = "CONTROLLER"
RECV_MASTER     = "MASTER"


# ─── Helpers bas niveau ──────────────────────────────────────────────────────

def _pad(s: str | bytes, n: int) -> bytes:
    """Encode en UTF-8 et complète avec des NUL jusqu'à n octets."""
    b = s.encode("utf-8", errors="replace") if isinstance(s, str) else s
    return b[:n].ljust(n, b"\x00")


def _str(b: bytes) -> str:
    """Décode des octets null-terminés en str."""
    return b.rstrip(b"\x00").decode("utf-8", errors="replace")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Lit exactement n octets depuis le socket (bloquant)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError(
                f"Connexion fermée prématurément ({len(buf)}/{n} octets reçus)"
            )
        buf.extend(chunk)
    return bytes(buf)


# ─── Sérialisation / désérialisation message_t ──────────────────────────────

def pack_message(mq_type: int, msg_type: str, recv_type: str, data: bytes = b"") -> bytes:
    """
    Construit un message_t complet (header + data).
    Le résultat est prêt à être envoyé via sendall().
    """
    header = struct.pack(
        _MSG_FMT,
        mq_type,
        _pad(msg_type, 64),
        _pad(recv_type, 64),
        len(data),
    )
    return header + data


def unpack_header(raw: bytes) -> dict:
    """
    Désérialise uniquement le header de message_t (144 octets).
    Retourne un dict {mq_type, type, recv_type, size}.
    """
    if len(raw) < MSG_HEADER_SIZE:
        raise ValueError(
            f"Header trop court : {len(raw)} < {MSG_HEADER_SIZE} octets"
        )
    mq_type, type_b, recv_type_b, size = struct.unpack_from(_MSG_FMT, raw)
    return {
        "mq_type": mq_type,
        "type": _str(type_b),
        "recv_type": _str(recv_type_b),
        "size": size,
    }


def recv_message(sock: socket.socket) -> dict:
    """
    Reçoit un message_t complet depuis le socket.
    Retourne un dict {mq_type, type, recv_type, size, data}.
    """
    header_raw = _recv_exact(sock, MSG_HEADER_SIZE)
    hdr = unpack_header(header_raw)
    data = _recv_exact(sock, hdr["size"]) if hdr["size"] > 0 else b""
    hdr["data"] = data
    return hdr


# ─── Sérialisation program_message_t ────────────────────────────────────────

def pack_program_message(program_name: str, code: bytes) -> bytes:
    """
    Construit un program_message_t selon les constantes de config.

    Layout :
        char     program_name[PROG_NAME_MAX]   null-padded
        uint32_t code_size                     longueur effective du code
        char     code[PROG_CODE_MAX]           code null-padded
    """
    prog_name_max: int = current_app.config["PROG_NAME_MAX"]
    prog_code_max: int = current_app.config["PROG_CODE_MAX"]

    if len(code) > prog_code_max:
        logger.warning(
            "Code source %d octets > PROG_CODE_MAX=%d — troncature",
            len(code), prog_code_max,
        )
        code = code[:prog_code_max]

    name_b = _pad(program_name, prog_name_max)
    code_size_b = struct.pack("=I", len(code))          # uint32_t
    code_b = code.ljust(prog_code_max, b"\x00")         # null-padded jusqu'à PROG_CODE_MAX

    return name_b + code_size_b + code_b


# ─── API publique ────────────────────────────────────────────────────────────

class DispatchError(Exception):
    """Erreur métier lors du dispatch TCP vers un agent."""


def discover_master_ip(controller_ip: str) -> str:
    """
    Interroge le contrôleur pour obtenir l'IP du nœud maître actuel.

    Protocole :
      → message_t { mq_type=1, type="DISCOVER_MASTER", recv_type="CONTROLLER", data="" }
      ← message_t { type="MASTER_IP", data="<ip.du.maitre>" }

    Retourne l'IP du maître (str).
    Lève DispatchError si la communication échoue.
    """
    port: int = current_app.config["CONTROLLER_DISPATCH_PORT"]
    timeout: float = current_app.config["DISPATCH_TIMEOUT_S"]

    msg = pack_message(
        mq_type=1,
        msg_type=MSG_DISCOVER_MASTER,
        recv_type=RECV_CONTROLLER,
    )

    logger.info("Requête DISCOVER_MASTER → contrôleur %s:%d", controller_ip, port)
    try:
        with socket.create_connection((controller_ip, port), timeout=timeout) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(msg)
            resp = recv_message(sock)
    except OSError as exc:
        raise DispatchError(
            f"Contrôleur {controller_ip}:{port} inaccessible : {exc}"
        ) from exc

    if resp["type"] == MSG_ERROR:
        reason = _str(resp["data"])
        raise DispatchError(f"Contrôleur a retourné une erreur : {reason}")

    master_ip = _str(resp["data"]).strip()
    if not master_ip:
        raise DispatchError("Le contrôleur a retourné une IP maître vide.")

    logger.info("Contrôleur désigne le maître à %s", master_ip)
    return master_ip


def send_programme_to_master(
    master_ip: str,
    programme_name: str,
    source_path: Path,
) -> int:
    """
    Envoie un programme au nœud maître via TCP.

    Protocole :
      → message_t { mq_type=2, type="PROGRAM", recv_type="MASTER",
                    data=program_message_t{ program_name, code_size, code } }
      ← message_t { type="ACK" }  (timeout silencieux si absent)

    Retourne le nombre d'octets de code envoyés.
    Lève DispatchError si la communication échoue.
    """
    port: int = current_app.config["MASTER_DISPATCH_PORT"]
    timeout: float = current_app.config["DISPATCH_TIMEOUT_S"]

    # Trouver le fichier source principal
    code, actual_path = _read_source_file(source_path, programme_name)

    # Construire program_message_t
    prog_msg = pack_program_message(programme_name, code)

    # Envelopper dans message_t
    msg = pack_message(
        mq_type=2,
        msg_type=MSG_PROGRAM,
        recv_type=RECV_MASTER,
        data=prog_msg,
    )

    logger.info(
        "Envoi programme '%s' (%d octets code, %d octets message) → maître %s:%d",
        programme_name, len(code), len(msg), master_ip, port,
    )

    try:
        with socket.create_connection((master_ip, port), timeout=timeout) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(msg)
            # Attente ACK (optionnel, 5 s max)
            sock.settimeout(5.0)
            try:
                resp = recv_message(sock)
                if resp["type"] == MSG_ERROR:
                    reason = _str(resp["data"])
                    raise DispatchError(f"Maître a refusé le programme : {reason}")
                logger.info("Maître a acquitté le programme (type=%s)", resp["type"])
            except socket.timeout:
                logger.debug("Pas d'ACK du maître dans les 5 s — on continue")
    except DispatchError:
        raise
    except OSError as exc:
        raise DispatchError(
            f"Maître {master_ip}:{port} inaccessible : {exc}"
        ) from exc

    return len(code)


# ─── Helpers internes ────────────────────────────────────────────────────────

# Priorité des extensions pour trouver le point d'entrée
_ENTRY_PRIORITY = [".py", ".c", ".cpp", ".java", ".f90", ".f", ".r", ".sh"]


def _read_source_file(source_dir: Path, programme_name: str) -> tuple[bytes, Path]:
    """
    Trouve et lit le fichier source principal dans source_dir.

    Stratégie :
      1. Cherche un fichier dont le nom (sans extension) correspond au
         nom du programme (insensible à la casse).
      2. Parmi tous les fichiers sources, prend celui avec l'extension
         la plus prioritaire.
      3. En dernier recours, prend le premier fichier trouvé.

    Retourne (contenu_bytes, chemin_absolu).
    Lève DispatchError si aucun fichier trouvé.
    """
    if not source_dir.exists():
        raise DispatchError(
            f"Répertoire source introuvable : {source_dir}"
        )

    all_files = [f for f in source_dir.rglob("*") if f.is_file()]
    if not all_files:
        raise DispatchError(
            f"Répertoire source vide : {source_dir}"
        )

    # 1. Correspondance par nom de programme
    prog_stem = programme_name.lower().replace(" ", "_").replace("-", "_")
    for f in all_files:
        if f.stem.lower() == prog_stem:
            logger.debug("Fichier source (correspondance nom) : %s", f)
            return f.read_bytes(), f

    # 2. Premier fichier selon la priorité des extensions
    for ext in _ENTRY_PRIORITY:
        for f in all_files:
            if f.suffix.lower() == ext:
                logger.debug("Fichier source (priorité ext %s) : %s", ext, f)
                return f.read_bytes(), f

    # 3. Fallback : premier fichier quelconque
    f = all_files[0]
    logger.debug("Fichier source (fallback) : %s", f)
    return f.read_bytes(), f
