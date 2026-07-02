# pbcalc

Usage: pbcalc COMMAND [ARGS]

Commands:

- `add NUM...` prints the sum of one or more numbers.
- `mul NUM...` prints the product of one or more numbers.
- `stats NUM...` prints `count`, `sum`, `mean`, `min`, and `max`.
- `--version` prints the program version.

Invalid numbers are rejected with an error message that includes the offending input.

Examples:

```bash
$ pbcalc --version
$ pbcalc add 2.5 -1.25 4
```
