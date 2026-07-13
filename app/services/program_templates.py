"""
Bibliothèque de programmes PARALLAX prêts à l'emploi ("templates").

Alternative à l'upload de code (POST /api/tasks/import) pour les utilisateurs
qui veulent juste exécuter un calcul connu (somme, produit matrice x vecteur…)
sans écrire de C : ils choisissent un template et fournissent uniquement des
fichiers .txt de données — un par "slot" déclaré dans `data_files` (ex. un
seul fichier de valeurs pour la somme, un fichier matrice + un fichier
vecteur pour la multiplication).

Les données sont injectées comme littéraux de tableau C directement dans le
template, plutôt que lues depuis un fichier externe à l'exécution : rien dans
le protocole de dispatch du cluster (CodeSubmission / program_message_t,
MAX_CODE_SIZE = 7500 - voir Execution_Master/utils/master_thread.h) ne permet
de faire voyager un payload de données à côté du code. SOURCE_SIZE_LIMIT
ci-dessous protège contre un dépassement de cette limite une fois le code
généré (template + données).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "program_templates"

# Garde de la marge sous MAX_CODE_SIZE (7500) pour les marqueurs
# __parallax_prog_name__ / __parallax_callback_host__ que tasks.py ajoute
# ensuite au moment du submit.
SOURCE_SIZE_LIMIT = 7000

_INT_RE = re.compile(r"-?\d+")


class TemplateError(Exception):
    """Données invalides ou incompatibles avec le template choisi."""


def _read_ints(data: bytes) -> list[int]:
    text = data.decode("utf-8", errors="replace")
    return [int(m) for m in _INT_RE.findall(text)]


def _c_array(values: list[int]) -> str:
    return ", ".join(str(v) for v in values)


def _build_sum(template_src: str, files: dict[str, bytes]) -> str:
    values = _read_ints(files["values"])
    if not values:
        raise TemplateError("Le fichier de données ne contient aucun entier.")
    literal = f"int payload[{len(values)}] = {{ {_c_array(values)} }};"
    return template_src.replace("/* __PARALLAX_DATA__ */", literal)


def _build_matrix_multiplication(template_src: str, files: dict[str, bytes]) -> str:
    matrix_values = _read_ints(files["matrix"])
    if len(matrix_values) < 2:
        raise TemplateError(
            "Fichier matrice : format attendu 'rows cols' suivi de rows×cols entiers "
            "(ligne par ligne)."
        )
    rows, cols = matrix_values[0], matrix_values[1]
    if rows <= 0 or cols <= 0:
        raise TemplateError("rows et cols doivent être des entiers strictement positifs.")

    matrix = matrix_values[2:]
    if len(matrix) != rows * cols:
        raise TemplateError(
            f"Fichier matrice : attendu {rows * cols} entiers ({rows}×{cols}) "
            f"après 'rows cols', reçu {len(matrix)}."
        )

    vector = _read_ints(files["vector"])
    if len(vector) != cols:
        raise TemplateError(
            f"Fichier vecteur : attendu {cols} entiers (= cols de la matrice), "
            f"reçu {len(vector)}."
        )

    literal = (
        f"int matrix[{rows * cols}] = {{ {_c_array(matrix)} }};\n"
        f"    int vector[{cols}] = {{ {_c_array(vector)} }};"
    )
    src = template_src.replace("__PARALLAX_COLS__", str(cols))
    return src.replace("/* __PARALLAX_DATA__ */", literal)


def _build_matrix_matrix_multiplication(template_src: str, files: dict[str, bytes]) -> str:
    a_values = _read_ints(files["matrix_a"])
    if len(a_values) < 2:
        raise TemplateError(
            "Fichier matrice A : format attendu 'rows cols' suivi de rows×cols entiers."
        )
    b_values = _read_ints(files["matrix_b"])
    if len(b_values) < 2:
        raise TemplateError(
            "Fichier matrice B : format attendu 'rows cols' suivi de rows×cols entiers."
        )

    rows_a, cols_a = a_values[0], a_values[1]
    rows_b, cols_b = b_values[0], b_values[1]
    if rows_a <= 0 or cols_a <= 0 or rows_b <= 0 or cols_b <= 0:
        raise TemplateError("rows et cols doivent être des entiers strictement positifs.")

    a_body = a_values[2:]
    if len(a_body) != rows_a * cols_a:
        raise TemplateError(
            f"Matrice A : attendu {rows_a * cols_a} entiers ({rows_a}×{cols_a}) "
            f"après 'rows cols', reçu {len(a_body)}."
        )

    b_body = b_values[2:]
    if len(b_body) != rows_b * cols_b:
        raise TemplateError(
            f"Matrice B : attendu {rows_b * cols_b} entiers ({rows_b}×{cols_b}) "
            f"après 'rows cols', reçu {len(b_body)}."
        )

    if cols_a != rows_b:
        raise TemplateError(
            f"Dimensions incompatibles pour la multiplication : cols de A ({cols_a}) "
            f"doit être égal à rows de B ({rows_b})."
        )

    literal = (
        f"int a[{rows_a * cols_a}] = {{ {_c_array(a_body)} }};\n"
        f"    int b[{rows_b * cols_b}] = {{ {_c_array(b_body)} }};"
    )
    src = template_src.replace("__PARALLAX_INNER__", str(cols_a))
    src = src.replace("__PARALLAX_COLS_B__", str(cols_b))
    return src.replace("/* __PARALLAX_DATA__ */", literal)


@dataclass(frozen=True)
class DataFileSlot:
    id: str
    label: str
    format: str
    # Forme des données pour ce slot, utilisée côté frontend pour choisir un
    # aperçu adapté (grille pour "matrix", ligne unique pour "vector", liste
    # simple pour "flat") - voir buildDataFilePreview() dans le frontend.
    kind: str  # "matrix" | "vector" | "flat"

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "format": self.format, "kind": self.kind}


@dataclass(frozen=True)
class ProgramTemplate:
    id: str
    name: str
    description: str
    filename: str
    builder: Callable[[str, dict[str, bytes]], str]
    data_files: list[DataFileSlot] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "data_files": [f.to_dict() for f in self.data_files],
        }

    def build_source(self, files: dict[str, bytes]) -> str:
        missing = [f.id for f in self.data_files if f.id not in files]
        if missing:
            raise TemplateError(f"Fichier(s) manquant(s) : {', '.join(missing)}.")

        template_src = (TEMPLATES_DIR / self.filename).read_text(encoding="utf-8")
        source = self.builder(template_src, files)
        if len(source.encode("utf-8")) > SOURCE_SIZE_LIMIT:
            raise TemplateError(
                f"Le jeu de données est trop volumineux pour ce template une fois "
                f"converti en code C (limite ~{SOURCE_SIZE_LIMIT} octets). "
                "Réduisez le nombre de valeurs."
            )
        return source


TEMPLATES: dict[str, ProgramTemplate] = {
    t.id: t for t in [
        ProgramTemplate(
            id="sum",
            name="Somme d'un tableau",
            description="Additionne une liste d'entiers, répartie sur le cluster.",
            filename="sum.c",
            builder=_build_sum,
            data_files=[
                DataFileSlot(
                    id="values",
                    label="Valeurs (.txt)",
                    format="Entiers séparés par des espaces ou des retours à la ligne.",
                    kind="flat",
                ),
            ],
        ),
        ProgramTemplate(
            id="matrix_multiplication",
            name="Multiplication matrice x vecteur",
            description="Multiplie une matrice par un vecteur, lignes réparties sur le cluster.",
            filename="matrix_multiplication.c",
            builder=_build_matrix_multiplication,
            data_files=[
                DataFileSlot(
                    id="matrix",
                    label="Matrice (.txt)",
                    format="'rows cols' puis rows×cols entiers, ligne par ligne.",
                    kind="matrix",
                ),
                DataFileSlot(
                    id="vector",
                    label="Vecteur (.txt)",
                    format="cols entiers (même valeur de cols que la matrice).",
                    kind="vector",
                ),
            ],
        ),
        ProgramTemplate(
            id="matrix_matrix_multiplication",
            name="Multiplication matrice x matrice",
            description="Multiplie deux matrices A x B, lignes de A réparties sur le cluster.",
            filename="matrix_matrix_multiplication.c",
            builder=_build_matrix_matrix_multiplication,
            data_files=[
                DataFileSlot(
                    id="matrix_a",
                    label="Matrice A (.txt)",
                    format="'rows cols' puis rows×cols entiers, ligne par ligne.",
                    kind="matrix",
                ),
                DataFileSlot(
                    id="matrix_b",
                    label="Matrice B (.txt)",
                    format="'rows cols' puis rows×cols entiers, ligne par ligne (rows de B = cols de A).",
                    kind="matrix",
                ),
            ],
        ),
    ]
}


def list_templates() -> list[dict]:
    return [t.to_dict() for t in TEMPLATES.values()]


def get_template(template_id: str) -> ProgramTemplate | None:
    return TEMPLATES.get(template_id)
