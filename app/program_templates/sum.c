#include <stdio.h>
#include <stdlib.h>
#include <string.h>

__attribute__((annotate("vcpus:2")))
__attribute__((annotate("reduce:sum_reduce")))
void *sum_array(void *data, size_t total_size) {
    int *arr = (int *)data;
    int count = (int)(total_size / sizeof(int));
    long long sum = 0;
    for (int i = 0; i < count; i++) {
        sum += arr[i];
    }
    printf("[WorkerTask] Partial sum: %lld over %d elements\n", sum, count);
    char *result = malloc(64);
    sprintf(result, "%lld", sum);
    return result;
}

int main() {
    printf("[SubmittedProg] Starting sum map-reduce...\n");

    /* __PARALLAX_DATA__ */

    sum_array(payload, sizeof(payload));

    printf("[SubmittedProg] Done.\n");
    return 0;
}
