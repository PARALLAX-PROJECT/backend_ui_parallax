#!/usr/bin/env python3
"""
Simulateur du nœud Maître PARALLAX.

Comportement :
  - Écoute sur MASTER_DISPATCH_PORT (défaut 9000)
  - Reçoit un message_t { type="PROGRAM", data=program_message_t }
  - Extrait et affiche le nom + les premières lignes du code reçu
  - Répond avec    message_t { type="ACK" }

Usage :
  python tools/master_sim.py [--port 9000] [--name-max 256] [--code-max 1048576]

Le simulateur reste actif jusqu'à Ctrl+C.
Chaque programme reçu est sauvegardé dans tools/received/<programme_name>.<ext>
"""
import argparse
import os
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import proto

# ─── Couleurs terminal ────────────────────────────────────────────────────────
MAGENTA = "\033[95m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
GRAY    = "\033[90m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

RECEIVED_DIR = Path(__file__).parent / "received"

def log(color, tag, msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{GRAY}[{ts}]{RESET} {color}{BOLD}[{tag}]{RESET} {msg}")

def info(msg):  log(MAGENTA, "MAÎTRE", msg)
def ok(msg):    log(GREEN,   "MAÎTRE", msg)
def warn(msg):  log(YELLOW,  "MAÎTRE", msg)
def err(msg):   log(RED,     "MAÎTRE", msg)


# ─── Handler de connexion ────────────────────────────────────────────────────

def handle_connection(conn: socket.socket, addr: tuple,
                      name_max: int, code_max: int):
    peer = f"{addr[0]}:{addr[1]}"
    info(f"Connexion entrante de {BOLD}{peer}{RESET}")
    try:
        conn.settimeout(30.0)
        msg = proto.recv_message(conn)

        info(f"  ← message_t {{ mq_type={msg['mq_type']}, type={msg['type']!r},"
             f" recv_type={msg['recv_type']!r}, size={msg['size']} o }}")

        if msg["type"] == "PROGRAM":
            _handle_program(msg["data"], name_max, code_max)
            # Envoi ACK
            proto.send_message(conn, mq_type=1, msg_type="ACK",
                               recv_type="BACKEND", data=b"OK")
            ok(f"  → ACK envoyé à {peer}")
        else:
            warn(f"  Type inconnu: {msg['type']!r}")
            proto.send_message(conn, mq_type=99, msg_type="ERROR",
                               recv_type="BACKEND",
                               data=f"Unknown type: {msg['type']}".encode())
    except Exception as exc:
        err(f"  Erreur sur {peer}: {exc}")
    finally:
        conn.close()
        info(f"  Connexion {peer} fermée")


def _handle_program(data: bytes, name_max: int, code_max: int):
    """Désérialise et affiche le programme reçu. Sauvegarde sur disque."""
    try:
        prog = proto.unpack_program(data, name_max=name_max)
    except Exception as exc:
        err(f"  Impossible de désérialiser program_message_t: {exc}")
        return

    name      = prog["program_name"]
    code_size = prog["code_size"]
    code      = prog["code"]

    ok(f"  Programme reçu !")
    print(f"    Nom          : {BOLD}{name}{RESET}")
    print(f"    Taille code  : {BOLD}{code_size}{RESET} octets"
          f"  ({code_size/1024:.1f} Ko)")
    print(f"    data[] total : {len(data)} octets")

    # Aperçu du code (10 premières lignes)
    try:
        code_str = code.decode("utf-8", errors="replace")
        lines = code_str.splitlines()[:10]
        print(f"    {GRAY}─── Aperçu du code ({min(10, len(lines))} premières lignes) ───{RESET}")
        for i, line in enumerate(lines, 1):
            print(f"    {GRAY}{i:3d}{RESET}  {line}")
        if len(code.decode('utf-8', errors='replace').splitlines()) > 10:
            print(f"    {GRAY}    … ({len(code.decode('utf-8', errors='replace').splitlines()) - 10} lignes de plus){RESET}")
    except Exception:
        print(f"    {GRAY}(code binaire — aperçu non disponible){RESET}")

    # Sauvegarde sur disque
    RECEIVED_DIR.mkdir(exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Garder l'extension d'origine si présente, sinon .txt
    ext = Path(safe_name).suffix or ".txt"
    stem = Path(safe_name).stem or "programme"
    out_path = RECEIVED_DIR / f"{stem}_{ts}{ext}"
    out_path.write_bytes(code)
    ok(f"  Code sauvegardé → {out_path}")


# ─── Serveur TCP ─────────────────────────────────────────────────────────────

def run_server(host: str, port: int, name_max: int, code_max: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(10)

    print()
    print(f"  {MAGENTA}{BOLD}╔══════════════════════════════════════════╗{RESET}")
    print(f"  {MAGENTA}{BOLD}║   PARALLAX — Simulateur Nœud Maître      ║{RESET}")
    print(f"  {MAGENTA}{BOLD}╚══════════════════════════════════════════╝{RESET}")
    print(f"  Écoute       : {BOLD}{host}:{port}{RESET}")
    print(f"  Header size  : {BOLD}{proto.HEADER_SIZE}{RESET} octets")
    print(f"  PROG_NAME_MAX: {BOLD}{name_max}{RESET}")
    print(f"  PROG_CODE_MAX: {BOLD}{code_max}{RESET} ({code_max//1024} Ko)")
    print(f"  Réception    : {RECEIVED_DIR}/")
    print(f"  Arrêt        : Ctrl+C")
    print()

    srv.settimeout(1.0)
    try:
        while True:
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=handle_connection,
                    args=(conn, addr, name_max, code_max),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Arrêt du simulateur maître.{RESET}")
    finally:
        srv.close()


# ─── Entrée ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulateur Maître PARALLAX")
    parser.add_argument("--host",      default="0.0.0.0",      help="Adresse d'écoute (défaut: 0.0.0.0)")
    parser.add_argument("--port",      type=int, default=9000,  help="Port d'écoute (défaut: 9000)")
    parser.add_argument("--name-max",  type=int, default=256,   help="MAX_PROGRAM_NAME (défaut: 256)")
    parser.add_argument("--code-max",  type=int, default=1_048_576, help="MAX_CODE_SIZE (défaut: 1048576)")
    args = parser.parse_args()
    run_server(args.host, args.port, args.name_max, args.code_max)
