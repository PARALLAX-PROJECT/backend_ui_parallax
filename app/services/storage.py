"""
Service de gestion des médias (fichiers projets) pour PARALLAX.

Contraintes de sécurité appliquées :
  - Path traversal : tout chemin de fichier est résolu et vérifié
    sous STORAGE_ROOT avant toute opération.
  - Zip bomb : la taille décompressée totale est limitée à MAX_UNCOMPRESSED_SIZE.
  - Nombre de fichiers dans une archive : limité à MAX_FILES_IN_ARCHIVE.
  - Extensions : seules les extensions autorisées sont acceptées.
  - Atomic write : écriture dans un fichier temporaire puis renommage.
  - Quota utilisateur : contrôle de l'espace consommé par utilisateur.
"""
import io
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

from flask import current_app
from werkzeug.utils import secure_filename


class StorageError(Exception):
    """Erreur métier du service de stockage."""


class QuotaExceededError(StorageError):
    """Le quota de stockage de l'utilisateur est dépassé."""


class InvalidFileError(StorageError):
    """Fichier invalide (extension, contenu ou chemin suspect)."""


class ArchiveBombError(StorageError):
    """Archive potentiellement malveillante (zip bomb ou trop volumineuse)."""


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _safe_path(base: Path, *parts: str) -> Path:
    """
    Construit un chemin en vérifiant qu'il reste sous `base`.
    Lève InvalidFileError si path traversal détecté.
    """
    candidate = (base / Path(*parts)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        raise InvalidFileError(
            f"Chemin suspect détecté (path traversal) : {'/'.join(parts)}"
        )
    return candidate


def _allowed_extension(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    cfg = current_app.config
    return (
        ext in cfg["ALLOWED_SOURCE_EXTENSIONS"]
        or ext in cfg["ALLOWED_ARCHIVE_EXTENSIONS"]
    )


def _is_archive(filename: str) -> bool:
    return Path(filename).suffix.lower() in current_app.config["ALLOWED_ARCHIVE_EXTENSIONS"]


# ──────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────

def project_source_dir(user_id: str, programme_id: str) -> Path:
    root: Path = current_app.config["STORAGE_ROOT"]
    return _safe_path(root, user_id, programme_id, "source")


def project_result_dir(user_id: str, programme_id: str) -> Path:
    root: Path = current_app.config["STORAGE_ROOT"]
    return _safe_path(root, user_id, programme_id, "results")


def save_project_source(
    user_id: str,
    programme_id: str,
    file_stream: BinaryIO,
    filename: str,
    current_usage_bytes: int,
) -> tuple[str, int]:
    """
    Sauvegarde le fichier source d'un programme.
    Retourne (chemin_relatif_depuis_STORAGE_ROOT, taille_en_octets).
    """
    cfg = current_app.config
    storage_root: Path = cfg["STORAGE_ROOT"]
    max_quota: int = cfg["MAX_STORAGE_PER_USER"]

    safe_name = secure_filename(filename)
    if not safe_name:
        raise InvalidFileError("Nom de fichier invalide.")
    if not _allowed_extension(safe_name):
        raise InvalidFileError(
            f"Extension non autorisée : {Path(safe_name).suffix!r}. "
            f"Extensions acceptées : {cfg['ALLOWED_SOURCE_EXTENSIONS'] | cfg['ALLOWED_ARCHIVE_EXTENSIONS']}"
        )

    source_dir = project_source_dir(user_id, programme_id)
    source_dir.mkdir(parents=True, exist_ok=True)

    if _is_archive(safe_name):
        total_bytes = _extract_archive(
            file_stream, safe_name, source_dir,
            cfg["MAX_UNCOMPRESSED_SIZE"],
            cfg["MAX_FILES_IN_ARCHIVE"],
        )
    else:
        # Fichier source simple → écriture atomique
        total_bytes = _write_single_file(file_stream, source_dir / safe_name)

    if current_usage_bytes + total_bytes > max_quota:
        # Nettoyer ce qu'on vient de poser avant de lever l'erreur
        shutil.rmtree(source_dir, ignore_errors=True)
        raise QuotaExceededError(
            f"Quota de stockage dépassé ({max_quota // (1024**3)} Go par utilisateur)."
        )

    rel_path = str((Path(user_id) / programme_id / "source").as_posix())
    return rel_path, total_bytes


def save_task_result(
    user_id: str,
    programme_id: str,
    task_id: str,
    data: bytes,
) -> tuple[str, int]:
    """
    Sauvegarde le résultat partiel d'une sous-tâche.
    Retourne (chemin_relatif, taille).
    """
    result_dir = project_result_dir(user_id, programme_id)
    result_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{task_id}.result"
    dest = _safe_path(result_dir, filename)
    size = _write_bytes_atomic(data, dest)

    rel_path = str(
        (Path(user_id) / programme_id / "results" / filename).as_posix()
    )
    return rel_path, size


def get_result_archive_path(user_id: str, programme_id: str) -> Path | None:
    """
    Retourne le chemin de l'archive de résultats si elle existe,
    sinon la crée à partir des fichiers .result présents.
    """
    result_dir = project_result_dir(user_id, programme_id)
    if not result_dir.exists():
        return None

    archive_path = result_dir / "results.zip"
    if archive_path.exists():
        return archive_path

    result_files = list(result_dir.glob("*.result"))
    if not result_files:
        return None

    _create_zip_archive(result_files, archive_path)
    return archive_path


def delete_project(user_id: str, programme_id: str) -> int:
    """
    Supprime tous les fichiers d'un projet.
    Retourne le nombre d'octets libérés.
    """
    root: Path = current_app.config["STORAGE_ROOT"]
    project_dir = _safe_path(root, user_id, programme_id)
    if not project_dir.exists():
        return 0
    freed = _dir_size(project_dir)
    shutil.rmtree(project_dir, ignore_errors=True)
    return freed


def user_storage_used(user_id: str) -> int:
    """Calcule l'espace disque total occupé par un utilisateur (octets)."""
    root: Path = current_app.config["STORAGE_ROOT"]
    user_dir = _safe_path(root, user_id)
    if not user_dir.exists():
        return 0
    return _dir_size(user_dir)


# ──────────────────────────────────────────────
# Implémentations internes
# ──────────────────────────────────────────────

def _write_single_file(stream: BinaryIO, dest: Path) -> int:
    """Écriture atomique d'un flux dans `dest`. Retourne la taille écrite."""
    parent = dest.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_")
    try:
        written = 0
        with os.fdopen(fd, "wb") as tmp_f:
            for chunk in _iter_chunks(stream):
                tmp_f.write(chunk)
                written += len(chunk)
        os.replace(tmp_path, dest)
        return written
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_bytes_atomic(data: bytes, dest: Path) -> int:
    parent = dest.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as tmp_f:
            tmp_f.write(data)
        os.replace(tmp_path, dest)
        return len(data)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _extract_archive(
    stream: BinaryIO,
    filename: str,
    dest_dir: Path,
    max_size: int,
    max_files: int,
) -> int:
    """
    Extrait une archive ZIP ou TAR.GZ dans dest_dir de manière sécurisée.
    Retourne la taille totale extraite.
    """
    data = stream.read()
    ext = Path(filename).suffix.lower()

    if ext == ".zip":
        return _extract_zip(io.BytesIO(data), dest_dir, max_size, max_files)
    elif ext in (".tar", ".gz", ".tgz") or filename.endswith(".tar.gz"):
        return _extract_tar(io.BytesIO(data), dest_dir, max_size, max_files)
    else:
        raise InvalidFileError(f"Format d'archive non supporté : {ext!r}")


def _extract_zip(
    stream: BinaryIO, dest_dir: Path, max_size: int, max_files: int
) -> int:
    try:
        zf = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise InvalidFileError(f"Archive ZIP invalide : {exc}") from exc

    entries = zf.infolist()
    if len(entries) > max_files:
        raise ArchiveBombError(
            f"L'archive contient {len(entries)} fichiers (max {max_files})."
        )

    total_uncompressed = sum(e.file_size for e in entries)
    if total_uncompressed > max_size:
        raise ArchiveBombError(
            f"Taille décompressée estimée ({total_uncompressed // (1024**2)} Mo) "
            f"dépasse la limite ({max_size // (1024**2)} Mo)."
        )

    extracted = 0
    for entry in entries:
        # Ignorer les répertoires
        if entry.filename.endswith("/"):
            continue
        # Sanitiser le chemin
        safe_name = _sanitize_archive_member(entry.filename)
        if safe_name is None:
            continue
        target = _safe_path(dest_dir, safe_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = zf.read(entry.filename)
        extracted += _write_bytes_atomic(content, target)
        if extracted > max_size:
            raise ArchiveBombError("Taille réelle extraite dépasse la limite.")

    return extracted


def _extract_tar(
    stream: BinaryIO, dest_dir: Path, max_size: int, max_files: int
) -> int:
    try:
        tf = tarfile.open(fileobj=stream, mode="r:*")
    except tarfile.TarError as exc:
        raise InvalidFileError(f"Archive TAR invalide : {exc}") from exc

    members = [m for m in tf.getmembers() if m.isfile()]
    if len(members) > max_files:
        raise ArchiveBombError(
            f"L'archive contient {len(members)} fichiers (max {max_files})."
        )

    total = sum(m.size for m in members)
    if total > max_size:
        raise ArchiveBombError(
            f"Taille décompressée ({total // (1024**2)} Mo) > limite ({max_size // (1024**2)} Mo)."
        )

    extracted = 0
    for member in members:
        safe_name = _sanitize_archive_member(member.name)
        if safe_name is None:
            continue
        target = _safe_path(dest_dir, safe_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        fobj = tf.extractfile(member)
        if fobj is None:
            continue
        extracted += _write_bytes_atomic(fobj.read(), target)
        if extracted > max_size:
            raise ArchiveBombError("Taille réelle extraite dépasse la limite.")

    return extracted


def _sanitize_archive_member(name: str) -> str | None:
    """
    Normalise un nom de membre d'archive.
    Retourne None si le membre doit être ignoré (répertoire, chemin absolu, traversal).
    """
    # Rejeter chemins absolus
    if name.startswith(("/", "\\")) or name.startswith(".."):
        return None
    # Normaliser les séparateurs
    parts = Path(name.replace("\\", "/")).parts
    # Filtrer les composants dangereux
    safe_parts = [p for p in parts if p not in (".", "..") and p != ""]
    if not safe_parts:
        return None
    result = str(Path(*safe_parts))
    # Vérifier l'extension du fichier final
    final_ext = Path(result).suffix.lower()
    allowed = (
        current_app.config["ALLOWED_SOURCE_EXTENSIONS"]
        | current_app.config["ALLOWED_ARCHIVE_EXTENSIONS"]
        | {".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".csv"}
    )
    if final_ext not in allowed:
        return None
    return result


def _create_zip_archive(files: list[Path], dest: Path) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=".tmp_results_", suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as tmp_f:
            with zipfile.ZipFile(tmp_f, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, f.name)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _iter_chunks(stream: BinaryIO, chunk_size: int = 65536):
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        yield chunk
