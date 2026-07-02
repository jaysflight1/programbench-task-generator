#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(void) {
    puts("Usage: calc COMMAND [ARGS]");
    puts("Commands:");
    puts("  add A B      Print A + B.");
    puts("Options:");
    puts("  -h, --help   Show help.");
    puts("  --version    Show version.");
}

int main(int argc, char **argv) {
    if (argc == 1 || strcmp(argv[1], "--help") == 0 || strcmp(argv[1], "-h") == 0) {
        usage();
        return 0;
    }
    if (strcmp(argv[1], "--version") == 0) {
        puts("ccalc 1.0.0");
        return 0;
    }
    if (strcmp(argv[1], "add") == 0 && argc == 4) {
        printf("%ld\n", strtol(argv[2], NULL, 10) + strtol(argv[3], NULL, 10));
        return 0;
    }
    fputs("unknown command\n", stderr);
    return 2;
}

