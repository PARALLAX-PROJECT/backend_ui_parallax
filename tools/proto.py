"""
Bibliothèque partagée du protocole binaire PARALLAX message_t.
Utilisée par les simulateurs et le script de test E2E.

Layout C (Linux 64-bit) :
    typedef struct {
        long      mq_type;       // 8 o  — natif 64-bit
        char      type[64];      // 64 o
        char      recv_type[64]; // 64 o
        uint64_t  size;          // 8 o
        char      data[];        // size octets suivent
    } message_t;
"""
import struct
import socket

# '@' = native byte order + native sizes (long = 8 octets sur Linux 64-bit)
_FMT = "@l64s64sQ"
HEADER_SIZE = struct.calcsize(_FMT)   # 144 octets


def _pad(s, n):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return b[:n].ljust(n, b"\x00")

def _str(b):
    return b.rstrip(b"\x00").decode("utf-8", errors="replace")


# ─── Sérialisation ────────────────────────────────────────────────────────────

def pack(mq_type: int, msg_type: str, recv_type: str, data: bytes = b"") -> bytes:
    """Construit un message_t complet (header + data)."""
    hdr = struct.pack(_FMT, mq_type, _pad(msg_type, 64), _pad(recv_type, 64), len(data))
    return hdr + data


def unpack_header(raw: bytes) -> dict:
    """Désérialise le header (HEADER_SIZE premiers octets)."""
    if len(raw) < HEADER_SIZE:
        raise ValueError(f"Header trop court: {len(raw)} < {HEADER_SIZE}")
    mq_type, type_b, recv_type_b, size = struct.unpack_from(_FMT, raw)
    return {
        "mq_type":   mq_type,
        "type":      _str(type_b),
        "recv_type": _str(recv_type_b),
        "size":      size,
    }


# ─── I/O socket ───────────────────────────────────────────────────────────────

def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError(f"Connexion fermée ({len(buf)}/{n} o reçus)")
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock: socket.socket) -> dict:
    """Reçoit un message_t complet. Retourne dict {mq_type, type, recv_type, size, data}."""
    hdr_raw = recv_exact(sock, HEADER_SIZE)
    hdr = unpack_header(hdr_raw)
    data = recv_exact(sock, hdr["size"]) if hdr["size"] > 0 else b""
    hdr["data"] = data
    return hdr


def send_message(sock: socket.socket, mq_type: int, msg_type: str,
                 recv_type: str, data: bytes = b"") -> None:
    sock.sendall(pack(mq_type, msg_type, recv_type, data))


# ─── program_message_t ────────────────────────────────────────────────────────

def pack_program(program_name: str, code: bytes,
                 name_max: int = 256, code_max: int = 1_048_576) -> bytes:
    """
    Sérialise un program_message_t :
        char     program_name[name_max]
        uint32_t code_size
        char     code[code_max]
    """
    code_trimmed = code[:code_max]
    name_b = _pad(program_name, name_max)
    size_b = struct.pack("=I", len(code_trimmed))
    code_b = code_trimmed.ljust(code_max, b"\x00")
    return name_b + size_b + code_b


def unpack_program(data: bytes, name_max: int = 256) -> dict:
    """Désérialise un program_message_t depuis les octets data."""
    if len(data) < name_max + 4:
        raise ValueError("program_message_t trop court")
    name_b   = data[:name_max]
    code_size = struct.unpack_from("=I", data, name_max)[0]
    code_start = name_max + 4
    code      = data[code_start: code_start + code_size]
    return {
        "program_name": _str(name_b),
        "code_size":    code_size,
        "code":         code,
    }
