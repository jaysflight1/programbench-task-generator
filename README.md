# ProgramBench Generator

ProgramBench Generator is a framework for creating ProgramBench-style benchmark
tasks from executable repositories. It builds a gold/reference executable,
discovers public behavior from docs and help output, generates executable-level
behavioral tests, evaluates those tests against the gold program, applies quality
gates, and writes cleanroom solver/evaluator packages plus reviewer-readable
reports.

The implementation is profile-driven and centered on a universal executable
test-case IR. Python remains the most mature path. C/C++ with Make, CMake, and
single-file compiler builds is supported for controlled local tasks and fixtures.
Go, Rust, and Java have conservative adapter skeletons with clear diagnostics and
coverage placeholders; they are extension paths, not a claim of full broad
ProgramBench parity yet.

## What It Produces

For each task profile, the pipeline can produce:

- `task_spec.yaml` with repository, build, language, and executable metadata.
- `behavior_surface.yaml` from docs, examples, help probes, and observed commands.
- `generated_tests/` containing canonical executable test cases and rendered pytest
  compatibility tests.
- Structured per-test outcomes with test name, file, duration, output snippets, and
  failure reason.
- Iteration reports for coverage, assertion linting, determinism, dummy rejection,
  redundancy, suite quality, reward shape, and efficiency.
- `qc_queue.md`, `qc_queue.json`, and `qc_queue.csv` for human review.
- `RUN_SUMMARY.md` and `reports/RUN_SUMMARY.json` for a concise explanation of what
  happened and what remains weak.
- Cleanroom `packages/solver/` and `packages/evaluator/` artifacts.
- A deterministic clean source export that excludes local caches, virtualenvs,
  generated runs, logs, and other workspace clutter.

## Architecture

```text
pbgen_task.yaml
-> source checkout or local source path
-> language adapter selection
-> gold/reference executable build
-> public behavior discovery
-> local or model-backed executable test-case generation
-> canonical test execution and pytest compatibility rendering
-> coverage, lint, determinism, dummy, redundancy, efficiency, and reward reports
-> QC queue and run summary
-> cleanroom solver/evaluator packages
-> clean source export
```

### Universal Executable Test Cases

Generated tests are represented first as canonical `ExecutableTestCase` records:
argv, stdin, env, fixtures, expected exit code, stdout/stderr assertions, timeout,
behavior category, and provenance. Pytest files are rendered as a compatibility
layer, but quality gates increasingly consume the canonical cases directly.

This matters because the benchmark representation is language-independent: Python,
C/C++, Go, Rust, Java, and future adapters can all produce the same executable
test-case shape.

### Language Adapters

The adapter registry maps a language/build-system pair to build, probing, coverage,
rendering, and package-runtime capabilities.

Current support:

| Language | Build systems | Coverage | Status |
|---|---|---|---|
| Python | scripts, packages, entrypoints | `coverage.py` | Most mature path |
| C/C++ | Make, CMake, single-file compiler | `gcov` when available | Supported for controlled local tasks |
| Go | `go build` | Explicit unavailable report | Conservative skeleton |
| Rust | `cargo build --release` | Explicit unavailable report | Conservative skeleton |
| Java | Maven, Gradle | Explicit unavailable report | Conservative skeleton |

Unsupported or unavailable capability paths produce structured diagnostics instead
of silently reporting success.

## Task Profiles

Create a `pbgen_task.yaml` for the repository you want to benchmark. Profiles keep
repository-specific choices outside the framework code.

Python package CLI example:

```yaml
task_id: python_pkgcalc
local_path: examples/robust_repos/python_package_cli
expected_language: python
primary_binary: pkgcalc
coverage_backend: python
dependency_policy: offline
trusted_local: true
iterations: 1
benchmark_commands:
  - ["--version"]
  - ["echo", "hello"]
safe_command_deny_patterns:
  - "rm"
  - "curl"
  - "wget"
```

C/Make CLI example:

```yaml
task_id: c_make_ccalc
local_path: examples/robust_repos/make_c_cli
expected_language: c/c++
primary_binary: ccalc
coverage_backend: c-family-gcov
dependency_policy: offline
trusted_local: true
iterations: 1
benchmark_commands:
  - ["--version"]
```

More examples live in `examples/profiles/`.

## Quickstart

Install the package locally:

```bash
cd programbench-task-generator
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Run the profile-driven pipeline:

```bash
pbgen run-task --profile examples/profiles/python_package_cli.pbgen_task.yaml \
  --iterations 1 --generation-backend local
```

Run the C/Make fixture when local compiler tools are available:

```bash
pbgen run-task --profile examples/profiles/c_make_cli.pbgen_task.yaml \
  --build-system make --iterations 1 --generation-backend local
```

For a model-backed run once an external adapter command is configured:

```bash
pbgen run-task --profile pbgen_task.yaml --iterations 2 --generation-backend model \
  --model-command "python path/to/model_adapter.py"
```

You can also run stages manually:

```bash
pbgen init-task --local-path examples/demo_task/source --task-id mini_cli
pbgen build-gold --task-id mini_cli
pbgen discover-surface --task-id mini_cli
pbgen generate-tests --task-id mini_cli --iterations 2 --generation-backend local
pbgen evaluate-suite --task-id mini_cli
pbgen package-cleanroom --task-id mini_cli
pbgen export-qc --task-id mini_cli
pbgen write-summary --task-id mini_cli
```

## Model-Backed Generation

The default backend is deterministic local generation. It extracts examples and
usage patterns from public docs/help output, records gold behavior, and writes
canonical executable cases plus pytest compatibility tests with exact stdout,
stderr, and exit-code assertions.

The model-backed backend is optional. When selected and configured with an external
model command, it uses the same task profile, behavior surface, coverage gaps, and
safety rules to propose structured executable test cases. Model output is treated as
untrusted: generated cases must pass validation and the same quality gates before
they are accepted. If no model command is configured, the tool fails clearly instead
of silently falling back or making an implicit network call.

## Security Model

Task execution is controlled by an execution policy:

- `trusted-local` for explicitly trusted local repos.
- `sandboxed-local` for conservative local execution with command filtering.
- `docker-no-network` for no-network container execution of candidate source
  builds and canonical hidden tests where Docker is available.

Profiles can declare safe command allow/deny patterns. Generated tests and model
proposals are validated before execution. Destructive/network/package-install
commands are rejected by default in probes and generated tests.

## Key Artifacts

Task outputs are written under `artifacts/<task_id>/`:

```text
artifacts/<task_id>/
  task_spec.yaml
  behavior_surface.yaml
  generated_tests/
    test_cases_iteration_0.json
    test_behavior_iter_0.py
  logs/generation_events.jsonl
  qc/
    qc_queue.json
    qc_queue.md
  reports/
    coverage_report_iteration_0.json
    lint_report_iteration_0.json
    redundancy_report_iteration_0.json
    suite_quality_report.json
    reward_shape_report.json
    efficiency_manifest.json
    RUN_SUMMARY.json
  RUN_SUMMARY.md
  packages/
    solver/
    evaluator/
```

The solver package contains only solver-visible materials such as public docs,
assets, `TASK.md`, `SUBMISSION.md`, and the public task spec. It does not include
the gold/reference executable. The evaluator package contains hidden tests, gold
executable material, reports, logs, and evaluator metadata. Leak checks are part of
the packaging flow.

## Validation

Before sharing a source export, run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy pbgen
```

Useful focused checks:

```bash
.venv/bin/pytest tests/integration/test_multilanguage_case_studies.py -q
.venv/bin/pytest tests/unit/test_language_adapters.py tests/unit/test_coverage_registry.py -q
pbgen --help
```

## Clean Source Export

For review or archival use, the project includes a deterministic export command:

```bash
pbgen export-submission --output dist/programbench-generator-submission.zip
```

The archive includes source, tests, prompts, examples, docs, config, and packaging
files while excluding local caches, generated benchmark artifacts, hidden run logs,
virtualenvs, build outputs, egg-info, and machine-specific files.

## Current Limits

- The system is not claiming complete arbitrary ProgramBench coverage across every
  compiled ecosystem.
- Python is the reference-quality language path.
- C/C++ support is credible for controlled local Make/CMake/single-file CLI repos,
  with coverage dependent on local `gcov`/compiler support.
- Go, Rust, and Java adapters currently provide build skeletons and structured
  diagnostics; coverage and richer behavior discovery remain planned extensions.
- Model-backed generation is optional and must be explicitly selected/configured.
- Docker/no-network execution depends on the local Docker runtime and a configured
  image. To run the optional live smoke test, set `PBGEN_DOCKER_TEST_IMAGE` to a
  locally available image, for example `python:3.11-slim`, then run:

```bash
PBGEN_DOCKER_TEST_IMAGE=python:3.11-slim .venv/bin/pytest tests/integration/test_docker_no_network_execution.py -q
```
- Real full-size repo runs should be treated as a separate validation step after
  selecting target repositories and profiles.

## Extending Languages

See `docs/adapter_development.md` for the adapter interface, expected capability
reports, coverage registry contract, and the checks to add when bringing up a new
language path.
