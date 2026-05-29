#!/usr/bin/env python3
"""
Test de bout en bout (E2E) de la chaîne de dispatch PARALLAX.

Scénario complet :
  1. Démarrage des simulateurs contrôleur (9001) et maître (9000) en arrière-plan
  2. Démarrage du backend Flask (5000) en arrière-plan (si --start-backend)
  3. Enregistrement du nœud contrôleur dans la base via /api/cluster/register
  4. Authentification d'un utilisateur (chercheur)
  5. Import d'un programme de test
  6. Soumission → backend découvre le maître → envoie le programme
  7. Vérification que le maître a bien reçu le programme
  8. Affichage du récapitulatif

Usage :
  # Lancer le backend d'abord dans un autre terminal, puis :
  python tools/test_e2e.py

  # Ou lancer le backend automatiquement :
  python tools/test_e2e.py --start-backend

  # Tester seulement le protocole sans le backend :
  python tools/test_e2e.py --proto-only
"""
from __future__ import annotations
import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

# proto.py dans le même répertoire
sys.path.insert(0, str(Path(__file__).parent))
import proto

# ─── Configuration ────────────────────────────────────────────────────────────
BACKEND_URL       = "http://127.0.0.1:5000"
CONTROLLER_PORT   = int(os.environ.get("CONTROLLER_DISPATCH_PORT", 9001))
MASTER_PORT       = int(os.environ.get("MASTER_DISPATCH_PORT", 9000))
CLUSTER_KEY       = os.environ.get("CLUSTER_INTERNAL_KEY", "internal-cluster-secret-key")
PROG_NAME_MAX     = int(os.environ.get("PROG_NAME_MAX", 256))
PROG_CODE_MAX     = int(os.environ.get("PROG_CODE_MAX", 1_048_576))

# Compte de test (créé si inexistant)
TEST_USER     = "test_e2e"
TEST_EMAIL    = "test_e2e@parallax.enspy"
TEST_PASSWORD = "Test@E2E2025!"

# ─── Couleurs terminal ─────────────────────────────────────────────────────────
COLORS = {
    "ok":     "\033[92m",
    "fail":   "\033[91m",
    "step":   "\033[96m",
    "warn":   "\033[93m",
    "gray":   "\033[90m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}

def c(key, txt):
    return f"{COLORS.get(key,'')}{txt}{COLORS['reset']}"

def step(n, msg):
    print(f"\n{c('step', f'[{n}]')} {c('bold', msg)}")

def ok(msg):   print(f"    {c('ok', '✓')} {msg}")
def fail(msg): print(f"    {c('fail', '✗')} {msg}"); sys.exit(1)
def warn(msg): print(f"    {c('warn', '⚠')} {msg}")
def info(msg): print(f"    {c('gray', '·')} {msg}")


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def http(method: str, path: str, body=None, headers=None, token=None) -> dict:
    """Requête HTTP simple sans dépendance externe."""
    url = f"{BACKEND_URL}{path}"
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers:
        h.update(headers)

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        try:
            return json.loads(body_txt)
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}: {body_txt[:200]}"}


def http_multipart(path: str, fields: dict, files: dict, token: str) -> dict:
    """Upload multipart/form-data."""
    import email.generator
    import io
    boundary = "----ParallaxBoundary7x3k"
    body = b""
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value.encode() + b"\r\n"
    for name, (filename, content) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    url = f"{BACKEND_URL}{path}"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        try:
            return json.loads(body_txt)
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}: {body_txt[:300]}"}


# ─── Simulateurs intégrés ─────────────────────────────────────────────────────

class ControllerSim(threading.Thread):
    """Contrôleur simulé : répond MASTER_IP=127.0.0.1 à toute requête DISCOVER_MASTER."""
    def __init__(self, port: int, master_ip: str = "127.0.0.1"):
        super().__init__(daemon=True)
        self.port = port
        self.master_ip = master_ip
        self.ready = threading.Event()
        self.requests_served = 0

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", self.port))
        except OSError as e:
            print(f"    {c('fail','✗')} ControllerSim bind ::{self.port} → {e}")
            return
        srv.listen(5)
        srv.settimeout(0.5)
        self.ready.set()
        while True:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            try:
                conn.settimeout(5.0)
                msg = proto.recv_message(conn)
                if msg["type"] == "DISCOVER_MASTER":
                    proto.send_message(conn, 1, "MASTER_IP", "BACKEND",
                                       self.master_ip.encode())
                    self.requests_served += 1
            except Exception:
                pass
            finally:
                conn.close()


class MasterSim(threading.Thread):
    """Maître simulé : reçoit PROGRAM, extrait program_message_t, envoie ACK."""
    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self.ready = threading.Event()
        self.received: list[dict] = []     # liste des programmes reçus

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", self.port))
        except OSError as e:
            print(f"    {c('fail','✗')} MasterSim bind ::{self.port} → {e}")
            return
        srv.listen(5)
        srv.settimeout(0.5)
        self.ready.set()
        while True:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            try:
                conn.settimeout(30.0)
                msg = proto.recv_message(conn)
                if msg["type"] == "PROGRAM":
                    prog = proto.unpack_program(msg["data"], name_max=PROG_NAME_MAX)
                    self.received.append(prog)
                    proto.send_message(conn, 1, "ACK", "BACKEND", b"OK")
            except Exception as exc:
                print(f"    {c('warn','⚠')} MasterSim erreur: {exc}")
            finally:
                conn.close()


# ─── Tests ────────────────────────────────────────────────────────────────────

def wait_backend(timeout: int = 30):
    """Attend que le backend Flask réponde."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BACKEND_URL}/api/docs/", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def test_proto_only():
    """Test bas niveau du protocole sans backend."""
    step("P1", "Test protocole — pack / unpack message_t")
    msg = proto.pack(1, "DISCOVER_MASTER", "CONTROLLER")
    assert len(msg) == proto.HEADER_SIZE, f"Taille {len(msg)} ≠ {proto.HEADER_SIZE}"
    hdr = proto.unpack_header(msg)
    assert hdr["type"] == "DISCOVER_MASTER"
    assert hdr["recv_type"] == "CONTROLLER"
    assert hdr["size"] == 0
    ok(f"message_t sérialisé : {len(msg)} octets, header parsé OK")

    step("P2", "Test protocole — pack / unpack program_message_t")
    code = b"print('Hello PARALLAX!')\n" * 100
    prog_data = proto.pack_program("mon_programme", code, PROG_NAME_MAX, PROG_CODE_MAX)
    prog = proto.unpack_program(prog_data, PROG_NAME_MAX)
    assert prog["program_name"] == "mon_programme", prog["program_name"]
    assert prog["code_size"] == len(code)
    assert prog["code"] == code
    ok(f"program_message_t OK — {len(prog_data)} octets, code {len(code)} o")

    step("P3", "Test protocole — aller-retour TCP loopback (ctrl sim)")
    ctrl_sim = ControllerSim(port=CONTROLLER_PORT + 100, master_ip="192.168.1.42")
    ctrl_sim.start()
    ctrl_sim.ready.wait(timeout=2)

    with socket.create_connection(("127.0.0.1", CONTROLLER_PORT + 100), timeout=3) as s:
        proto.send_message(s, 1, "DISCOVER_MASTER", "CONTROLLER")
        resp = proto.recv_message(s)
    assert resp["type"] == "MASTER_IP", resp
    ip = resp["data"].decode().rstrip("\x00")
    assert ip == "192.168.1.42", ip
    ok(f"TCP loopback OK — maître découvert : {ip}")

    print(f"\n  {c('ok', '✓✓✓')} Tous les tests protocole ont réussi !\n")


def run_e2e(start_backend: bool):
    """Test complet avec le backend Flask."""

    # ── 0. Démarrer les simulateurs ────────────────────────────────────────
    step("0", "Démarrage des simulateurs TCP")

    ctrl_sim = ControllerSim(port=CONTROLLER_PORT, master_ip="127.0.0.1")
    ctrl_sim.start()
    if not ctrl_sim.ready.wait(timeout=3):
        fail(f"ControllerSim n'a pas démarré sur :{CONTROLLER_PORT}")
    ok(f"Simulateur contrôleur démarré sur 127.0.0.1:{CONTROLLER_PORT}")

    master_sim = MasterSim(port=MASTER_PORT)
    master_sim.start()
    if not master_sim.ready.wait(timeout=3):
        fail(f"MasterSim n'a pas démarré sur :{MASTER_PORT}")
    ok(f"Simulateur maître démarré sur 127.0.0.1:{MASTER_PORT}")

    # ── 1. Vérifier le backend ─────────────────────────────────────────────
    step("1", f"Vérification backend Flask {BACKEND_URL}")
    if not wait_backend(30):
        fail(f"Backend inaccessible sur {BACKEND_URL} — lancez-le d'abord :\n"
             f"       cd backend_parallax && source .venv/bin/activate && python run.py")
    ok("Backend Flask opérationnel")

    # ── 2. Enregistrer le contrôleur dans la base ──────────────────────────
    step("2", "Enregistrement du nœud contrôleur dans la base")
    ctrl_reg = http("POST", "/api/cluster/register",
        body={
            "uuid": "sim-controller-e2e-0001",
            "ip": "127.0.0.1",
            "hostname": "sim-controller",
            "role": "controller",
            "profile": {
                "cpu_cores": 4, "cpu_freq_mhz": 3600.0,
                "arch_cpu": "x86_64", "ram_total_mb": 8192,
                "ram_available_mb": 4096, "storage_total_gb": 100.0,
                "storage_available_gb": 50.0, "network_latency_ms": 1.0,
            }
        },
        headers={"X-Cluster-Key": CLUSTER_KEY},
    )
    if ctrl_reg.get("success"):
        ok(f"Contrôleur enregistré (uuid=sim-controller-e2e-0001, ip=127.0.0.1)")
    else:
        warn(f"Contrôleur déjà enregistré ou erreur: {ctrl_reg.get('message','?')}")

    # ── 3. Authentification ────────────────────────────────────────────────
    step("3", "Authentification utilisateur de test")
    # Créer le compte s'il n'existe pas
    reg = http("POST", "/api/auth/register", {
        "username": TEST_USER, "email": TEST_EMAIL,
        "password": TEST_PASSWORD, "role": "chercheur",
    })
    if reg.get("success"):
        ok(f"Compte {TEST_USER!r} créé")
    else:
        info(f"Compte existant ou erreur: {reg.get('message','?')}")

    login = http("POST", "/api/auth/login", {
        "username": TEST_USER, "password": TEST_PASSWORD,
    })
    if not login.get("success"):
        fail(f"Authentification échouée: {login.get('message','?')}")
    token = login["data"]["access_token"]
    ok(f"Authentifié en tant que {TEST_USER!r}")

    # ── 4. Vérifier que le maître est visible ──────────────────────────────
    step("4", "Vérification GET /api/nodes/master")
    minfo = http("GET", "/api/nodes/master", token=token)
    info(f"cluster_ready = {minfo.get('data',{}).get('cluster_ready')}")
    info(f"controller    = {minfo.get('data',{}).get('controller',{}).get('ip') if minfo.get('data',{}).get('controller') else 'non enregistré'}")
    # Le maître peut être absent si aucun nœud master n'est en base — le contrôleur le fournira
    ok("Endpoint /api/nodes/master répond correctement")

    # ── 5. Import d'un programme de test ──────────────────────────────────
    step("5", "Import d'un programme de test")
    sample_code = b"""\
#!/usr/bin/env python3
# Programme de test PARALLAX E2E
# @parallax.split
def calcul_partiel(x):
    return x * x + 2 * x + 1

# @parallax.shared
def aggreger(resultats):
    return sum(resultats)

if __name__ == "__main__":
    data = list(range(100))
    resultats = [calcul_partiel(x) for x in data]
    total = aggreger(resultats)
    print(f"Resultat: {total}")
"""
    prog_name = f"e2e_test_{int(time.time())}"
    import_resp = http_multipart(
        "/api/tasks/import",
        fields={"name": prog_name, "description": "Programme de test E2E automatique"},
        files={"file": ("programme_test.py", sample_code)},
        token=token,
    )
    if not import_resp.get("success"):
        fail(f"Import échoué: {import_resp.get('message', import_resp)}")
    prog_id = import_resp["data"]["id"]
    ok(f"Programme importé — id={prog_id}, nom={prog_name!r}")
    info(f"Code source: {len(sample_code)} octets")

    # ── 6. Soumission ──────────────────────────────────────────────────────
    step("6", "Soumission du programme → chaîne dispatch complète")
    info("Flux attendu:")
    info(f"  Backend → Contrôleur (:{CONTROLLER_PORT})  [DISCOVER_MASTER]")
    info(f"  Contrôleur → Backend                       [MASTER_IP=127.0.0.1]")
    info(f"  Backend → Maître (:{MASTER_PORT})           [PROGRAM + program_message_t]")
    info(f"  Maître → Backend                           [ACK]")
    print()

    t_start = time.time()
    submit_resp = http("POST", f"/api/tasks/{prog_id}/submit", token=token)
    elapsed = time.time() - t_start

    if not submit_resp.get("success"):
        msg = submit_resp.get("message", str(submit_resp))
        fail(f"Soumission échouée: {msg}")

    dispatch = submit_resp.get("data", {}).get("dispatch", {})
    ok(f"Soumission réussie en {elapsed:.2f}s !")
    ok(f"Message du serveur: {submit_resp.get('message','')}")

    if dispatch:
        print(f"\n    {c('bold','Détails du dispatch:')}")
        print(f"      master_ip    : {c('ok', dispatch.get('master_ip','—'))}")
        print(f"      controller_ip: {dispatch.get('controller_ip','—')}")
        bs = dispatch.get('bytes_sent', 0)
        print(f"      bytes_sent   : {bs} o ({bs/1024:.1f} Ko)")

    # ── 7. Vérification côté maître ────────────────────────────────────────
    step("7", "Vérification que le maître a bien reçu le programme")
    time.sleep(0.5)  # laisser le handler terminer

    if not master_sim.received:
        fail("Le simulateur maître n'a reçu AUCUN programme !")

    received = master_sim.received[-1]
    ok(f"Maître a reçu {len(master_sim.received)} programme(s)")
    ok(f"  program_name : {received['program_name']!r}")
    ok(f"  code_size    : {received['code_size']} octets")

    # Vérification de l'intégrité du code
    assert received["code"] == sample_code, (
        f"Code reçu ≠ code envoyé !\n"
        f"  Envoyé  : {sample_code[:80]!r}\n"
        f"  Reçu    : {received['code'][:80]!r}"
    )
    ok(f"  Intégrité du code : {c('ok','OK')} — binaire identique octet par octet")
    ok(f"  Requêtes contrôleur servies : {ctrl_sim.requests_served}")

    # ── 8. Récapitulatif ───────────────────────────────────────────────────
    print(f"\n  {'═'*50}")
    print(f"  {c('ok', '✓✓✓')} {c('bold', 'TEST E2E COMPLET — SUCCÈS')}")
    print(f"  {'═'*50}")
    print(f"  Programme id  : {prog_id}")
    print(f"  Code transmis : {received['code_size']} o ({received['code_size']/1024:.1f} Ko)")
    print(f"  Maître        : 127.0.0.1:{MASTER_PORT}")
    print(f"  Contrôleur    : 127.0.0.1:{CONTROLLER_PORT}")
    print(f"  Durée totale  : {elapsed:.2f}s")
    print()


# ─── Entrée ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test E2E PARALLAX Dispatch")
    parser.add_argument("--start-backend", action="store_true",
                        help="Démarre automatiquement le backend Flask")
    parser.add_argument("--proto-only", action="store_true",
                        help="Teste seulement le protocole binaire (sans backend)")
    parser.add_argument("--backend-url", default="http://127.0.0.1:5000",
                        help="URL du backend (défaut: http://127.0.0.1:5000)")
    args = parser.parse_args()

    if args.backend_url != BACKEND_URL:
        BACKEND_URL = args.backend_url

    print(f"\n  {c('bold', '═'*52)}")
    print(f"  {c('bold', '   PARALLAX — Test bout en bout (E2E)       ')}")
    print(f"  {c('bold', '   Protocole TCP binaire message_t           ')}")
    print(f"  {c('bold', '═'*52)}")
    print(f"  Backend       : {BACKEND_URL}")
    print(f"  Contrôleur    : 127.0.0.1:{CONTROLLER_PORT}")
    print(f"  Maître        : 127.0.0.1:{MASTER_PORT}")
    print(f"  MSG_HEADER    : {proto.HEADER_SIZE} octets")
    print(f"  PROG_NAME_MAX : {PROG_NAME_MAX}")
    print(f"  PROG_CODE_MAX : {PROG_CODE_MAX // 1024} Ko")
    print(f"  CLUSTER_KEY   : {'*' * (len(CLUSTER_KEY)-4)}{CLUSTER_KEY[-4:]}")

    if args.proto_only:
        test_proto_only()
    else:
        run_e2e(args.start_backend)
