#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Reduce aggregator: concatenates each worker's partial output rows with ';'. */
void *my_concat_reduce(void *a, void *b) {
    if (!a && !b) return NULL;
    if (!a) { char *r = malloc(strlen((char *)b) + 1); strcpy(r, (char *)b); return r; }
    if (!b) { char *r = malloc(strlen((char *)a) + 1); strcpy(r, (char *)a); return r; }
    size_t len = strlen((char *)a) + strlen((char *)b) + 2;
    char *res = malloc(len);
    snprintf(res, len, "%s;%s", (char *)a, (char *)b);
    return res;
}

__attribute__((annotate("vcpus:2")))
__attribute__((annotate("reduce:my_concat_reduce")))
void *matvec_mult(void *matrix_data, size_t matrix_size, void *vector_data, size_t vector_size) {
    (void)vector_size;
    const int cols = __PARALLAX_COLS__;
    int *matrix = (int *)matrix_data;
    int *vector = (int *)vector_data;
    int rows = (int)(matrix_size / (cols * sizeof(int)));

    printf("[WorkerTask] Computing %d row(s) x %d col(s) matrix-vector product\n", rows, cols);

    char *result = malloc(256);
    int pos = 0;
    for (int i = 0; i < rows; i++) {
        long long sum = 0;
        for (int j = 0; j < cols; j++) {
            sum += (long long)matrix[i * cols + j] * vector[j];
        }
        pos += snprintf(result + pos, 256 - pos, i > 0 ? ",%lld" : "%lld", sum);
    }
    return result;
}

int main() {
    printf("[SubmittedProg] Starting distributed matrix-vector multiply...\n");

    /* __PARALLAX_DATA__ */

    matvec_mult(matrix, sizeof(matrix), vector, sizeof(vector));

    printf("[SubmittedProg] Done.\n");
    return 0;
}
