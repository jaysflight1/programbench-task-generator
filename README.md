# ProgramBench Generator

ProgramBench Generator is a Python-first framework for creating ProgramBench-style
benchmark tasks from executable repositories. It builds a gold/reference executable,
discovers public behavior from docs and help output, generates behavioral pytest tests,
evaluates those tests against the gold program, applies quality gates, and writes
cleanroom solver/evaluator packages plus reviewer-readable reports.

The current product is scoped only to Python CLIs. More language support coming within 
the next few days. C/Make and other compiled-repo flows are extension points or limited
fixture paths unless the local build backend can run them safely and deterministically.
This repository is not claiming complete ProgramBench generality across arbitrary
compiled ecosystems yet.

## What It Produces

For each task profile, the pipeline can produce:

- `task_spec.yaml` with repository, build, and executable metadata.
- `behavior_surface.yaml` from docs, examples, help probes, and observed commands.
- `generated_tests/` with append-only generated pytest files.
- Structured per-test outcomes with test name, file, duration, output snippets, and
  failure reason.
- Iteration reports for coverage, assertion linting, determinism, dummy rejection,
  redundancy, suite quality, and reward shape.
- `qc_queue.md`, `qc_queue.json`, and `qc_queue.csv` for human review.
- `RUN_SUMMARY.md` and `reports/RUN_SUMMARY.json` for a concise explanation of what happened.
- Cleanroom `packages/solver/` and `packages/evaluator/` artifacts.
- A clean submission archive that excludes local caches, virtualenvs, generated runs,
  logs, and other workspace clutter.

## Pipeline

```text
pbgen_task.yaml
-> source checkout or local source path
-> gold/reference executable
-> public behavior discovery
-> local or model-backed test generation
-> gold execution with structured per-test outcomes
-> coverage, lint, determinism, dummy, redundancy, and reward reports
-> QC queue and run summary
-> cleanroom solver/evaluator packages
-> clean submission export
```

## Generation Backends

The default backend is deterministic local generation. It extracts examples and usage
patterns from public docs/help output, records gold behavior, and writes pytest tests
with exact stdout, stderr, and exit-code assertions.

The model-backed backend is optional. When selected and configured with an external
model command, it uses the same task profile, behavior surface, coverage gaps, and
safety rules to propose tests. Model output is treated as untrusted, meaning generated 
tests must pass validation and the same quality gates before they are accepted. If no 
model command is configured, the tool fails clearly rather than silently falling back 
or making an implicit network call.

## Minimal Task Profile

Create a `pbgen_task.yaml` for the repository you want to benchmark. For the included
demo fixture:

```yaml
task_id: mini_cli
local_path: examples/demo_task/source
expected_language: python
primary_binary: pbcalc
coverage_backend: python
dependency_policy: offline
trusted_local: false
iterations: 2
benchmark_commands:
  - ["add", "2", "3"]
  - ["stats", "1", "2", "3"]
safe_command_deny_patterns:
  - "rm"
  - "curl"
  - "wget"
```

Prefer task profiles over hardcoded repo behavior. Profiles make runs reproducible and
keep repository-specific choices outside the framework code.

## Quickstart

Install the package locally:

```bash
cd programbench-generator
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Run the profile-driven pipeline:

```bash
pbgen run-task --profile pbgen_task.yaml --iterations 2 --generation-backend local
```

For a model-backed run once an adapter command is configured:

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

Create a clean submission artifact:

```bash
pbgen export-submission --output dist/programbench-generator-submission.zip
```

## Key Artifacts

Task outputs are written under `artifacts/<task_id>/`:

```text
artifacts/<task_id>/
  behavior_surface.yaml
  generated_tests/
  logs/generation_events.jsonl
  qc/
  reports/
  RUN_SUMMARY.md
  packages/
    solver/
    evaluator/
```

The solver package contains only solver-visible materials such as the executable,
public docs, assets, and `TASK.md`. The evaluator package contains hidden tests,
gold executable material, reports, logs, and evaluator metadata. Leak checks are part
of the packaging flow.

## Validation

Before sharing a submission artifact, run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/python -m mypy pbgen
```

These checks cover the core schemas, task-profile loading, structured test results,
coverage-guided generation loop, quality reports, run summaries, and packaging paths.

## Clean Source Export

For review or archival use, the project includes a deterministic export command:

```bash
pbgen export-submission --output dist/programbench-generator-submission.zip
```
## Current Limits

- Python CLI repositories are the first production-quality target.
- Model-backed generation is optional and must be explicitly selected/configured.
- The deterministic local backend remains the default and is suitable for reproducible
  tests and CI.
- Compiled-repo support is not the main claim of this version. C/Make fixtures exercise
  parts of the build abstraction, but broad Rust, Go, Java, CMake, Maven, Gradle, Docker,
  and dependency-fetching support are extension points.
- Benchmark efficiency scoring relies on declared benchmark commands in the task profile.
  It does not yet automatically discover a full runtime corpus.
