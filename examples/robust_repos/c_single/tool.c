#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc == 1 || strcmp(argv[1], "--help") == 0) {
        puts("Usage: tool COMMAND [ARGS]");
        return 0;
    }
    if (strcmp(argv[1], "mul") == 0 && argc == 4) {
        printf("%ld\n", strtol(argv[2], NULL, 10) * strtol(argv[3], NULL, 10));
        return 0;
    }
    fputs("unknown command\n", stderr);
    return 2;
}
