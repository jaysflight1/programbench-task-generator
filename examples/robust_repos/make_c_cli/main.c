#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
  if (argc < 2 || strcmp(argv[1], "--help") == 0) {
    printf("Usage: ccalc COMMAND [ARGS]\n");
    return 0;
  }
  if (strcmp(argv[1], "--version") == 0) {
    printf("ccalc 0.1\n");
    return 0;
  }
  fprintf(stderr, "unknown command: %s\n", argv[1]);
  return 2;
}
