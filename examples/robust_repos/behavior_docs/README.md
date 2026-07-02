# Behavior Discovery Fixture

Usage: program COMMAND [ARGS]

Commands:
  echo TEXT       Print text.
  file FILE      Print a file path.
  env            Print APP_MODE.

Options:
  --config PATH  Load settings.toml.

The tool can read from stdin / standard input. Set APP_MODE=test for stable output.

Examples:

```sh
$ program --help
$ program echo hello
$ program file sample.txt
$ program --config settings.toml
$ program delete all
```

Invalid commands print an error.
