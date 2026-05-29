#!/usr/bin/env python3
"""
Simulateur du nœud Contrôleur PARALLAX.

Comportement :
  - Écoute sur CONTROLLER_DISPATCH_PORT (défaut 9001)
  - Reçoit un message_t { type="DISCOVER_MASTER" }
  - Répond avec    message_t { type="MASTER_IP", data="<MASTER_IP>" }

Usage :
  python tools/controller_sim.py [--port 9001] [--master-ip 127.0.0.1]

Le simulateur reste actif jusqu'à Ctrl+C.
"""
import argparse
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

# Accès à proto.py depuis le même répertoire
sys.path.insert(0, str(Path(__file__).parent))
import proto

# ─── Couleurs terminal ────────────────────────────────────────────────────────
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
GRAY   = "\033[90m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def log(color, tag, msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{GRAY}[{ts}]{RESET} {color}{BOLD}[{tag}]{RESET} {msg}")

def info(msg):  log(CYAN,   "CTRL", msg)
def ok(msg):    log(GREEN,  "CTRL", msg)
def warn(msg):  log(YELLOW, "CTRL", msg)
def err(msg):   log(RED,    "CTRL", msg)


# ─── Handler de connexion ────────────────────────────────────────────────────

def handle_connection(conn: socket.socket, addr: tuple, master_ip: str):
    peer = f"{addr[0]}:{addr[1]}"
    info(f"Connexion entrante de {BOLD}{peer}{RESET}")
    try:
        conn.settimeout(10.0)
        msg = proto.recv_message(conn)

        info(f"  ← message_t {{ mq_type={msg['mq_type']}, type={msg['type']!r},"
             f" recv_type={msg['recv_type']!r}, size={msg['size']} }}")

        if msg["type"] == "DISCOVER_MASTER":
            ok(f"  Requête DISCOVER_MASTER reçue → réponse MASTER_IP={BOLD}{master_ip}{RESET}")
            proto.send_message(conn, mq_type=1, msg_type="MASTER_IP",
                               recv_type="BACKEND", data=master_ip.encode())
            ok(f"  → Réponse envoyée")
        else:
            warn(f"  Type de message inconnu: {msg['type']!r} — envoi ERR")
            proto.send_message(conn, mq_type=99, msg_type="ERROR",
                               recv_type="BACKEND",
                               data=f"Unknown type: {msg['type']}".encode())
    except Exception as exc:
        err(f"  Erreur sur {peer}: {exc}")
    finally:
        conn.close()
        info(f"  Connexion {peer} fermée")


# ─── Serveur TCP ─────────────────────────────────────────────────────────────

def run_server(host: str, port: int, master_ip: str):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(10)

    print()
    print(f"  {GREEN}{BOLD}╔══════════════════════════════════════════╗{RESET}")
    print(f"  {GREEN}{BOLD}║   PARALLAX — Simulateur Contrôleur       ║{RESET}")
    print(f"  {GREEN}{BOLD}╚══════════════════════════════════════════╝{RESET}")
    print(f"  Écoute        : {BOLD}{host}:{port}{RESET}")
    print(f"  Maître annoncé: {BOLD}{master_ip}{RESET}")
    print(f"  Header size   : {BOLD}{proto.HEADER_SIZE}{RESET} octets")
    print(f"  Arrêt         : Ctrl+C")
    print()

    srv.settimeout(1.0)  # pour intercepter KeyboardInterrupt
    try:
        while True:
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=handle_connection, args=(conn, addr, master_ip), daemon=True
                )
                t.start()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Arrêt du simulateur contrôleur.{RESET}")
    finally:
        srv.close()


# ─── Entrée ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulateur Contrôleur PARALLAX")
    parser.add_argument("--host",      default="0.0.0.0",   help="Adresse d'écoute (défaut: 0.0.0.0)")
    parser.add_argument("--port",      type=int, default=9001, help="Port d'écoute (défaut: 9001)")
    parser.add_argument("--master-ip", default="127.0.0.1", help="IP du maître à annoncer (défaut: 127.0.0.1)")
    args = parser.parse_args()
    run_server(args.host, args.port, args.master_ip)
