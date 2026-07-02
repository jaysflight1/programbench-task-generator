# Adapter Development Guide

This project uses language adapters to keep repository-specific build and coverage
logic out of the core pipeline. The coordinator-facing contract lives in
`pbgen.languages.adapters`.

## Adapter Responsibilities

A language adapter should answer four questions:

- Can this language/build-system pair be built?
- Can public behavior be probed safely?
- Can coverage be measured, or should the system emit an explicit unavailable report?
- Can canonical executable test cases be rendered or executed for this runtime?

The adapter returns a `LanguageCapabilityReport` with those answers. Unsupported or
partially supported paths should be honest: set unsupported fields to `False`, add a
clear reason or warning, and let the pipeline produce diagnostics rather than silent
success.

## Core Interfaces

Add or update a `LanguageAdapter` implementation with:

```python
class ExampleLanguageAdapter(LanguageAdapter):
    name = "example"
    languages = frozenset({"example"})
    build_systems = frozenset({"example-build"})

    def capability_report(self, *, language: str | None, build_system: str | None) -> LanguageCapabilityReport:
        ...

    def build_backend(self, config: PBGenConfig, *, build_system_override: str | None) -> BuildBackend:
        ...
```

Register the adapter in `LanguageAdapterRegistry`. Keep the registry deterministic:
specific adapters should appear before fallback/unsupported behavior.

## Build Backend Expectations

Builds should produce a `BuildArtifact` with:

- a primary executable at `gold/executable/program`
- all discovered executables recorded in `executable_paths`
- structured `build_attempts`
- a readable `build.log`
- runtime dependencies such as `python3` or `java` when needed

Build failures should raise `BuildError` and write enough structured detail for a
reviewer to understand whether the problem was a missing toolchain, unsupported build
system, timeout, policy block, or command failure.

Do not fetch dependencies or run arbitrary custom build commands unless the profile
explicitly marks the task trusted and the execution policy allows the command.

## Coverage Registry

Coverage is selected separately in `pbgen.coverage.registry`.

Production coverage adapters should return a normal `CoverageReport` with normalized
gaps. Incomplete paths should return `coverage_unavailable_report(...)` and write:

- `coverage_report_iteration_<n>.json`
- `coverage_unavailable_report_iteration_<n>.json`
- `coverage_unavailable_report.json`

This keeps scoring honest and avoids pretending unsupported coverage worked.

## Canonical Test Cases

Adapters should not require language-specific test formats as the source of truth.
Generated behavior should become `ExecutableTestCase` records with:

- `args`
- `stdin`
- `env`
- `fixture_files`
- expected exit code
- stdout/stderr exact, contains, or regex assertions
- timeout
- behavior category
- provenance/source evidence

The universal runner can execute these directly against any executable. Pytest files
are rendered for compatibility and review.

## Behavior Discovery

Discovery should prefer public, stable surfaces:

- README and docs examples
- `--help`, `-h`, and `--version`
- native tests that reveal CLI usage
- declared profile examples
- safe probes that pass command filtering

Destructive, network, package-install, mutation, and path-traversal commands should be
filtered before probing.

## Tests To Add For A New Adapter

Add focused tests for:

- adapter selection and capability report
- command construction
- missing toolchain diagnostics
- successful fixture build where the toolchain is stable in CI
- coverage available/unavailable behavior
- canonical test execution against the built executable
- cleanroom package artifacts when included in an end-to-end fixture

Then run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy pbgen
```

For a fully supported language path, add an integration fixture similar to
`tests/integration/test_multilanguage_case_studies.py`.
