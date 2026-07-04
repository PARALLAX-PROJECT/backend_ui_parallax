"""Generate PARALLAX C programs from guided .txt data uploads."""
from __future__ import annotations

import re
from pathlib import Path


class PredefinedProgramError(Exception):
    """Invalid guided calculation input."""


_INT_RE = re.compile(r"[-+]?\d+")
MAX_SUM_VALUES = 1500
MAX_MATRIX_ROWS = 200
MAX_MATRIX_COLS = 200


def generate_predefined_program(calculation_type: str, txt_path: Path, output_dir: Path) -> tuple[Path, int]:
    """Parse a .txt data file and write the generated PARALLAX C source."""
    try:
        text = txt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PredefinedProgramError("Le fichier .txt doit etre encode en UTF-8.") from exc

    kind = (calculation_type or "").strip().lower()
    if kind == "sum":
        code = _generate_sum_program(_parse_ints(text))
        filename = "generated_sum_parallax.c"
    elif kind == "matrix":
        rows, cols, matrix, vector = _parse_matrix_vector(text)
        code = _generate_matvec_program(rows, cols, matrix, vector)
        filename = "generated_matrix_parallax.c"
    else:
        raise PredefinedProgramError("Type de calcul guide inconnu.")

    output_path = output_dir / filename
    output_path.write_text(code, encoding="utf-8")
    return output_path, len(code.encode("utf-8"))


def _parse_ints(text: str) -> list[int]:
    values = [int(m.group(0)) for m in _INT_RE.finditer(text)]
    if not values:
        raise PredefinedProgramError("Le fichier de somme doit contenir au moins un entier.")
    if len(values) > MAX_SUM_VALUES:
        raise PredefinedProgramError(f"Trop de valeurs pour la somme ({len(values)} > {MAX_SUM_VALUES}).")
    return values


def _parse_matrix_vector(text: str) -> tuple[int, int, list[int], list[int]]:
    lines = []
    for raw in text.splitlines():
        cleaned = raw.split("#", 1)[0].strip()
        if cleaned:
            lines.append(cleaned)

    if not lines:
        raise PredefinedProgramError("Le fichier matriciel est vide.")

    separator_idx = _find_vector_separator(lines)
    if separator_idx is not None:
        matrix_lines = lines[:separator_idx]
        vector_lines = lines[separator_idx + 1:]
        if not matrix_lines:
            raise PredefinedProgramError("La matrice doit apparaitre avant la ligne VECTOR.")
        if len(vector_lines) != 1:
            raise PredefinedProgramError("Apres VECTOR, ajoutez une seule ligne contenant le vecteur.")

        parsed_rows = [_line_ints(line) for line in matrix_lines]
        vector = _line_ints(vector_lines[0])
        rows = len(parsed_rows)
        cols = len(parsed_rows[0]) if parsed_rows else 0
    else:
        rows, cols, parsed_rows, vector = _parse_legacy_matrix_vector(lines)

    if rows <= 0 or cols <= 0:
        raise PredefinedProgramError("Les dimensions de la matrice doivent etre positives.")
    if rows > MAX_MATRIX_ROWS or cols > MAX_MATRIX_COLS:
        raise PredefinedProgramError(
            f"Dimensions trop grandes ({rows}x{cols}, max {MAX_MATRIX_ROWS}x{MAX_MATRIX_COLS})."
        )

    matrix: list[int] = []
    for idx, row in enumerate(parsed_rows, start=1):
        if len(row) != cols:
            raise PredefinedProgramError(
                f"La ligne {idx} de la matrice contient {len(row)} valeur(s), attendu {cols}."
            )
        matrix.extend(row)

    if len(vector) != cols:
        raise PredefinedProgramError(
            f"Le vecteur contient {len(vector)} valeur(s), attendu {cols}."
        )
    return rows, cols, matrix, vector


def _find_vector_separator(lines: list[str]) -> int | None:
    for idx, line in enumerate(lines):
        marker = line.strip().upper().replace(":", "")
        if marker in {"VECTOR", "VECTEUR"}:
            return idx
    return None


def _parse_legacy_matrix_vector(lines: list[str]) -> tuple[int, int, list[list[int]], list[int]]:
    first = _line_ints(lines[0])
    if len(first) != 2:
        raise PredefinedProgramError(
            "Format matriciel attendu: lignes de matrice, puis une ligne VECTOR, puis le vecteur."
        )

    rows, cols = first
    if len(lines) < rows + 2:
        raise PredefinedProgramError(
            f"Il faut {rows} lignes de matrice puis une ligne vecteur de {cols} valeurs."
        )

    parsed_rows = [_line_ints(lines[idx + 1]) for idx in range(rows)]
    vector = _line_ints(lines[rows + 1])
    return rows, cols, parsed_rows, vector

def _line_ints(line: str) -> list[int]:
    return [int(m.group(0)) for m in _INT_RE.finditer(line)]


def _c_int_array(values: list[int], indent: str = "    ", per_line: int = 12) -> str:
    chunks = []
    for i in range(0, len(values), per_line):
        chunks.append(indent + ", ".join(str(v) for v in values[i:i + per_line]))
    return ",\n".join(chunks)


def _generate_sum_program(values: list[int]) -> str:
    payload = _c_int_array(values)
    return f"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void *my_sum_reduce(void *a, void *b) {{
    if (!a && !b) return NULL;
    long long val_a = a ? atoll((char *)a) : 0;
    long long val_b = b ? atoll((char *)b) : 0;
    char *res = malloc(64);
    if (!res) return NULL;
    sprintf(res, \"%lld\", val_a + val_b);
    return res;
}}

__attribute__((annotate(\"vcpus:2\")))
__attribute__((annotate(\"reduce:my_sum_reduce\")))
void *sum_array(void *data, size_t total_size) {{
    int *arr = (int *)data;
    int count = (int)(total_size / sizeof(int));
    long long sum = 0;
    for (int i = 0; i < count; i++) sum += arr[i];
    printf(\"[WorkerTask] Got %d elements, first=%d last=%d, partial sum=%lld\\n\",
           count, count > 0 ? arr[0] : 0, count > 0 ? arr[count - 1] : 0, sum);
    char *result = malloc(64);
    if (!result) return NULL;
    sprintf(result, \"%lld\", sum);
    return result;
}}

int main(void) {{
    printf(\"[SubmittedProg] Starting guided distributed sum...\\n\");
    int payload[{len(values)}] = {{
{payload}
    }};
    printf(\"[SubmittedProg] Input values: {len(values)}\\n\");
    sum_array(payload, sizeof(payload));
    printf(\"[SubmittedProg] Done.\\n\");
    return 0;
}}
"""


def _generate_matvec_program(rows: int, cols: int, matrix: list[int], vector: list[int]) -> str:
    matrix_payload = _c_int_array(matrix)
    vector_payload = _c_int_array(vector)
    result_size = max(256, rows * 24)
    return f"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ROWS {rows}
#define COLS {cols}

void *my_concat_reduce(void *a, void *b) {{
    if (!a && !b) return NULL;
    if (!a) {{ char *r = malloc(strlen((char *)b) + 1); if (r) strcpy(r, (char *)b); return r; }}
    if (!b) {{ char *r = malloc(strlen((char *)a) + 1); if (r) strcpy(r, (char *)a); return r; }}
    size_t len = strlen((char *)a) + strlen((char *)b) + 2;
    char *res = malloc(len);
    if (!res) return NULL;
    snprintf(res, len, \"%s;%s\", (char *)a, (char *)b);
    return res;
}}

__attribute__((annotate(\"vcpus:2\")))
__attribute__((annotate(\"reduce:my_concat_reduce\")))
void *matvec_mult(void *matrix_data, size_t matrix_size, void *vector_data, size_t vector_size) {{
    (void)vector_size;
    const int cols = {cols};
    int *matrix = (int *)matrix_data;
    int *vector = (int *)vector_data;
    int rows = (int)(matrix_size / (cols * sizeof(int)));

    printf(\"[WorkerTask] Computing %d row(s) x %d col(s) matrix-vector product\\n\", rows, cols);

    char *result = malloc({result_size});
    if (!result) return NULL;
    int pos = 0;
    for (int i = 0; i < rows; i++) {{
        long long sum = 0;
        for (int j = 0; j < cols; j++) sum += (long long)matrix[i * cols + j] * vector[j];
        printf(\"[WorkerTask] row %d result=%lld\\n\", i, sum);
        pos += snprintf(result + pos, {result_size} - pos, i > 0 ? \",%lld\" : \"%lld\", sum);
    }}
    return result;
}}

int main(void) {{
    printf(\"[SubmittedProg] Starting guided distributed matrix-vector multiply...\\n\");
    printf(\"[SubmittedProg] Matrix dimensions: %dx%d\\n\", ROWS, COLS);
    int matrix[ROWS * COLS] = {{
{matrix_payload}
    }};
    int vector[COLS] = {{
{vector_payload}
    }};
    matvec_mult(matrix, sizeof(matrix), vector, sizeof(vector));
    printf(\"[SubmittedProg] Done.\\n\");
    return 0;
}}
"""
