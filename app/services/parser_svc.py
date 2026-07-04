"""
Invokes Parser/build/mytool from the backend, moving the parsing step off
the C master (see Execution_Master/utils/master_thread.c: it now compiles
whatever it receives directly, without re-parsing). This lets a chercheur
preview the parser's rewrite ("view parsed code") and see parse errors
before ever touching the cluster, instead of finding out only after a
distributed submission already went out.

Parser/build/mytool is the same Clang libTooling binary the master used to
shell out to: it finds functions annotated with
__attribute__((annotate("vcpus:N"))) / __attribute__((annotate("reduce:..")))
and rewrites the call site into a scatter/broadcast/reduce dispatch stub
(see Parser/parser.cpp). Nothing about that rewrite logic changes here -
only *where* it runs.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class ParseError(Exception):
    """Le Parser a échoué ou n'a produit aucun fichier `_parsed.c`."""


def _parser_binary_and_root() -> tuple[Path, Path]:
    """
    Localise Parser/build/mytool et la racine du projet (pour -I<root>),
    en remontant depuis ce fichier :
    backend_ui_parallax/app/services/parser_svc.py -> .../GOAT-Plugs/
    """
    project_root = Path(__file__).resolve().parents[3]
    binary = project_root / "Parser" / "build" / "mytool"
    if not binary.exists():
        raise ParseError(
            f"Parser introuvable à {binary} — lancez `make` à la racine du "
            f"projet (compile aussi Parser/build/mytool)."
        )
    return binary, project_root


def parse_source(code: bytes, prog_id: str, timeout_s: float = 15.0) -> tuple[str, str]:
    """
    Lance Parser/build/mytool sur `code` et retourne (parsed_code, parser_log).

    `prog_id` (l'UUID du Programme) sert de nom de fichier temporaire : le
    parser embarque le nom du fichier source qu'on lui donne comme
    `__parallax_prog_name__` dans sa sortie (voir Parser/parser.cpp). Un nom
    fixe comme "submission.c" ferait que TOUS les programmes parsés
    porteraient le même `__parallax_prog_name__`, et donc écraseraient le
    même fichier de log côté Receptionist (logs/submission.c.log) une fois
    exécutés en parallèle — exactement le bug que ce marqueur est censé
    éviter (voir _with_prog_name_marker dans tasks.py).

    Lève ParseError si le parser échoue ou ne produit pas de fichier
    `_parsed.c` (aucune fonction annotée, erreur de syntaxe C, etc.) -
    parser_log explique pourquoi (mêmes messages que ceux vus dans
    progs/<id>.c.parse_err quand c'était encore le master qui parsait).
    """
    binary, project_root = _parser_binary_and_root()

    with tempfile.TemporaryDirectory(prefix="parallax_parse_") as tmpdir:
        src_path = Path(tmpdir) / f"{prog_id}.c"
        src_path.write_bytes(code)
        parsed_path = Path(f"{src_path}_parsed.c")

        cmd = [str(binary), str(src_path), "--", f"-I{project_root}"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s,
                cwd=project_root,
            )
        except subprocess.TimeoutExpired as exc:
            raise ParseError(f"Le parser a dépassé le délai de {timeout_s}s.") from exc
        except OSError as exc:
            raise ParseError(f"Impossible de lancer le parser : {exc}") from exc

        log = (proc.stdout or "") + (proc.stderr or "")

        if proc.returncode != 0 or not parsed_path.exists():
            raise ParseError(log or "Le parser a échoué sans sortie (code annoté manquant ?).")

        return parsed_path.read_text(errors="replace"), log
