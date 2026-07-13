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
void *matmat_mult(void *a_data, size_t a_size, void *b_data, size_t b_size) {
    (void)b_size;
    const int inner = __PARALLAX_INNER__;   /* cols of A == rows of B */
    const int cols_b = __PARALLAX_COLS_B__; /* cols of B == cols of the result */
    int *a = (int *)a_data;
    int *b = (int *)b_data;
    int rows_a = (int)(a_size / (inner * sizeof(int)));

    printf("[WorkerTask] Computing %d row(s) of A (inner=%d) x B (%d x %d)\n", rows_a, inner, inner, cols_b);

    char *result = malloc((size_t)rows_a * cols_b * 24 + 1);
    int pos = 0;
    for (int i = 0; i < rows_a; i++) {
        for (int j = 0; j < cols_b; j++) {
            long long sum = 0;
            for (int k = 0; k < inner; k++) {
                sum += (long long)a[i * inner + k] * b[k * cols_b + j];
            }
            pos += sprintf(result + pos, (i == 0 && j == 0) ? "%lld" : ",%lld", sum);
        }
    }
    return result;
}

int main() {
    printf("[SubmittedProg] Starting distributed matrix-matrix multiply...\n");

    /* __PARALLAX_DATA__ */

    matmat_mult(a, sizeof(a), b, sizeof(b));

    printf("[SubmittedProg] Done.\n");
    return 0;
}
