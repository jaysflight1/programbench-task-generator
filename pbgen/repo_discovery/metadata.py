"""Deterministic repository metadata extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re
import stat
import tomllib


IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}

ENTRYPOINT_SKIP_DIRS = {"test", "tests"}
COMMON_NON_OUTPUT_TARGETS = {"all", "check", "clean", "distclean", "install", "test"}
DOC_DIR_NAMES = {"docs", "examples", "man"}
DOC_FILE_PREFIXES = ("readme", "changelog", "contributing", "usage")
ASSET_SUFFIXES = {".csv", ".dat", ".json", ".txt"}
PYTHON_MANIFESTS = {
    "Pipfile",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}

MANIFEST_KINDS = {
    "Cargo.toml": ("rust", "cargo"),
    "CMakeLists.txt": ("c/c++", "cmake"),
    "GNUmakefile": ("make", "make"),
    "Makefile": ("make", "make"),
    "Pipfile": ("python", "dependency-lock"),
    "build.gradle": ("java", "gradle"),
    "go.mod": ("go", "go"),
    "makefile": ("make", "make"),
    "package-lock.json": ("javascript", "npm-lock"),
    "package.json": ("javascript", "npm"),
    "poetry.lock": ("python", "dependency-lock"),
    "pom.xml": ("java", "maven"),
    "pyproject.toml": ("python", "project"),
    "requirements.txt": ("python", "requirements"),
    "setup.cfg": ("python", "setuptools"),
    "setup.py": ("python", "setuptools"),
}

MAKEFILE_NAMES = {"GNUmakefile", "Makefile", "makefile"}
EXECUTABLE_SOURCE_SUFFIXES = {"", ".py", ".sh", ".pl", ".rb", ".js"}


@dataclass(frozen=True)
class BuildCandidate:
    """Ranked build strategy discovered from repository structure."""

    build_system: str
    language: str | None
    confidence: float
    commands: tuple[tuple[str, ...], ...] = ()
    output_hints: tuple[str, ...] = ()
    dependency_manifests: tuple[str, ...] = ()
    entrypoint_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_system": self.build_system,
            "language": self.language,
            "confidence": self.confidence,
            "commands": [list(command) for command in self.commands],
            "output_hints": list(self.output_hints),
            "dependency_manifests": list(self.dependency_manifests),
            "entrypoint_paths": list(self.entrypoint_paths),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EntrypointCandidate:
    """Ranked executable or wrapper target discovered in a repository."""

    name: str
    path: str
    invocation_kind: str
    confidence: float
    reason: str
    help_probe_supported: bool = True
    language: str | None = None
    module: str | None = None
    callable_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "invocation_kind": self.invocation_kind,
            "confidence": self.confidence,
            "reason": self.reason,
            "help_probe_supported": self.help_probe_supported,
        }
        if self.module:
            data["module"] = self.module
        if self.callable_name:
            data["callable_name"] = self.callable_name
        if self.language:
            data["language"] = self.language
        return data


@dataclass(frozen=True)
class DependencyManifest:
    """Dependency or build manifest discovered in stable repository order."""

    path: str
    ecosystem: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "ecosystem": self.ecosystem, "kind": self.kind}


@dataclass(frozen=True)
class RepositoryAnalysis:
    """Full deterministic repository analysis used by discovery and build."""

    primary_language: str | None
    primary_build_system: str | None
    build_candidates: tuple[BuildCandidate, ...] = ()
    entrypoint_candidates: tuple[EntrypointCandidate, ...] = ()
    dependency_manifests: tuple[DependencyManifest, ...] = ()
    docs_paths: tuple[str, ...] = ()
    asset_paths: tuple[str, ...] = ()
    metadata_warnings: tuple[str, ...] = ()

    @property
    def dependency_manifest_paths(self) -> list[str]:
        return [manifest.path for manifest in self.dependency_manifests]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "primary_build_system": self.primary_build_system,
            "build_candidates": [candidate.to_dict() for candidate in self.build_candidates],
            "entrypoint_candidates": [
                candidate.to_dict() for candidate in self.entrypoint_candidates
            ],
            "dependency_manifests": [manifest.to_dict() for manifest in self.dependency_manifests],
            "docs_paths": list(self.docs_paths),
            "asset_paths": list(self.asset_paths),
            "metadata_warnings": list(self.metadata_warnings),
        }


def analyze_repository(repo_path: Path, primary_binary: str | None = None) -> RepositoryAnalysis:
    """Return deterministic ranked metadata for a local repository checkout."""

    files = _iter_files(repo_path)
    docs_paths = tuple(_detect_docs(repo_path))
    asset_paths = tuple(_detect_assets(repo_path, docs_paths))
    dependency_manifests = tuple(_detect_dependency_manifests(repo_path, files))
    entrypoint_candidates = tuple(_rank_entrypoints(repo_path, files, primary_binary))
    build_candidates = tuple(
        _rank_build_candidates(repo_path, files, dependency_manifests, entrypoint_candidates)
    )

    warnings: list[str] = []
    if not build_candidates:
        warnings.append("no supported build strategy detected")
    if not entrypoint_candidates:
        warnings.append("no executable entrypoint detected")
    if not docs_paths:
        warnings.append("no documentation paths detected")
    if len(entrypoint_candidates) > 1:
        top_confidence = entrypoint_candidates[0].confidence
        tied = [
            candidate.path
            for candidate in entrypoint_candidates
            if candidate.confidence == top_confidence
        ]
        if len(tied) > 1:
            warnings.append(
                "multiple equally ranked entrypoints; deterministic path ordering selected "
                f"{entrypoint_candidates[0].path}"
            )

    for candidate in build_candidates:
        warnings.extend(candidate.warnings)

    primary_build = build_candidates[0] if build_candidates else None
    primary_entrypoint = entrypoint_candidates[0] if entrypoint_candidates else None
    primary_language = (
        primary_build.language
        if primary_build and primary_build.language
        else primary_entrypoint.language if primary_entrypoint else None
    )
    primary_build_system = primary_build.build_system if primary_build else None

    return RepositoryAnalysis(
        primary_language=primary_language,
        primary_build_system=primary_build_system,
        build_candidates=build_candidates,
        entrypoint_candidates=entrypoint_candidates,
        dependency_manifests=dependency_manifests,
        docs_paths=docs_paths,
        asset_paths=asset_paths,
        metadata_warnings=tuple(dict.fromkeys(warnings)),
    )


def detect_language(repo_path: Path) -> tuple[str | None, str | None]:
    """Detect the highest-ranked implementation language and build system."""

    analysis = analyze_repository(repo_path)
    return analysis.primary_language, analysis.primary_build_system


def detect_docs(repo_path: Path) -> list[str]:
    """Return stable relative paths for likely documentation files/directories."""

    return list(analyze_repository(repo_path).docs_paths)


def detect_assets(repo_path: Path) -> list[str]:
    """Return stable relative paths for asset-like files."""

    return list(analyze_repository(repo_path).asset_paths)


def detect_dependency_manifests(repo_path: Path) -> list[str]:
    """Return stable relative paths for dependency and build manifests."""

    return analyze_repository(repo_path).dependency_manifest_paths


def detect_binaries(repo_path: Path) -> list[str]:
    """Return likely executable entrypoints in deterministic ranked order."""

    config_path = repo_path / "pbgen_task.json"
    if config_path.exists():
        return []
    return [candidate.path for candidate in analyze_repository(repo_path).entrypoint_candidates]


def _rank_build_candidates(
    repo_path: Path,
    files: list[Path],
    manifests: tuple[DependencyManifest, ...],
    entrypoints: tuple[EntrypointCandidate, ...],
) -> list[BuildCandidate]:
    manifest_paths = {manifest.path for manifest in manifests}
    candidates: list[BuildCandidate] = []

    makefile = _first_existing(repo_path, MAKEFILE_NAMES)
    if makefile:
        makefile_rel = _rel(repo_path, makefile)
        output_hints = tuple(_parse_make_output_hints(makefile))
        warnings: list[str] = []
        if not output_hints:
            warnings.append("make build detected without obvious executable target hints")
        candidates.append(
            BuildCandidate(
                build_system="make",
                language=_infer_make_language(files),
                confidence=0.95,
                commands=(("make",),),
                output_hints=output_hints,
                dependency_manifests=tuple(
                    sorted(path for path in manifest_paths if path == makefile_rel)
                ),
                warnings=tuple(warnings),
            )
        )

    python_manifests = tuple(
        sorted(path for path in manifest_paths if Path(path).name in PYTHON_MANIFESTS)
    )
    pyproject = repo_path / "pyproject.toml"
    setup_py = repo_path / "setup.py"
    setup_cfg = repo_path / "setup.cfg"
    if pyproject.exists() or setup_py.exists() or setup_cfg.exists():
        package_entrypoints = tuple(
            candidate.path
            for candidate in entrypoints
            if candidate.invocation_kind in {"python-entrypoint", "python-module"}
        )
        candidates.append(
            BuildCandidate(
                build_system="python-package",
                language="python",
                confidence=0.9 if package_entrypoints else 0.82,
                commands=(),
                dependency_manifests=python_manifests,
                entrypoint_paths=package_entrypoints,
            )
        )

    python_script_entrypoints = tuple(
        candidate.path
        for candidate in entrypoints
        if candidate.invocation_kind in {"python-script", "executable-file"}
        and candidate.language == "python"
    )
    if python_script_entrypoints:
        candidates.append(
            BuildCandidate(
                build_system="script",
                language="python",
                confidence=0.84,
                commands=(),
                entrypoint_paths=python_script_entrypoints,
            )
        )

    single_c_source = _single_c_family_source(repo_path, files)
    if single_c_source is not None:
        language = "c++" if single_c_source.suffix.lower() in {".cpp", ".cc", ".cxx"} else "c"
        candidates.append(
            BuildCandidate(
                build_system="c-single",
                language=language,
                confidence=0.76,
                commands=((_single_source_compiler_name(language), _rel(repo_path, single_c_source)),),
                output_hints=(single_c_source.stem,),
                entrypoint_paths=(_rel(repo_path, single_c_source),),
            )
        )

    executable_entrypoints = tuple(
        candidate.path
        for candidate in entrypoints
        if candidate.invocation_kind == "executable-file"
        and candidate.language not in {"python"}
    )
    if executable_entrypoints:
        candidates.append(
            BuildCandidate(
                build_system="script",
                language="script",
                confidence=0.72,
                commands=(),
                entrypoint_paths=executable_entrypoints,
            )
        )

    candidates.extend(_manifest_only_candidates(manifests))
    return _dedupe_build_candidates(candidates)


def _manifest_only_candidates(manifests: tuple[DependencyManifest, ...]) -> list[BuildCandidate]:
    candidates: list[BuildCandidate] = []
    by_path = {manifest.path: manifest for manifest in manifests}
    if "Cargo.toml" in by_path:
        candidates.append(
            BuildCandidate(
                build_system="cargo",
                language="rust",
                confidence=0.72,
                commands=(("cargo", "build", "--release"),),
                dependency_manifests=("Cargo.toml",),
                warnings=("cargo build detected but local backend does not build rust yet",),
            )
        )
    if "go.mod" in by_path:
        candidates.append(
            BuildCandidate(
                build_system="go",
                language="go",
                confidence=0.72,
                commands=(("go", "build", "./..."),),
                dependency_manifests=("go.mod",),
                warnings=("go build detected but local backend does not build go yet",),
            )
        )
    if "pom.xml" in by_path:
        candidates.append(
            BuildCandidate(
                build_system="maven",
                language="java",
                confidence=0.68,
                commands=(("mvn", "package"),),
                dependency_manifests=("pom.xml",),
                warnings=("maven build detected but local backend does not build java yet",),
            )
        )
    if "build.gradle" in by_path:
        candidates.append(
            BuildCandidate(
                build_system="gradle",
                language="java",
                confidence=0.68,
                commands=(("gradle", "build"),),
                dependency_manifests=("build.gradle",),
                warnings=("gradle build detected but local backend does not build java yet",),
            )
        )
    if "CMakeLists.txt" in by_path:
        candidates.append(
            BuildCandidate(
                build_system="cmake",
                language="c/c++",
                confidence=0.68,
                commands=(("cmake", "-S", ".", "-B", "build"), ("cmake", "--build", "build")),
                dependency_manifests=("CMakeLists.txt",),
                warnings=(
                    "cmake build detected but local backend does not build cmake projects yet",
                ),
            )
        )
    return candidates


def _dedupe_build_candidates(candidates: list[BuildCandidate]) -> list[BuildCandidate]:
    by_system: dict[str, BuildCandidate] = {}
    for candidate in candidates:
        existing = by_system.get(candidate.build_system)
        if existing is None or _build_sort_key(candidate) < _build_sort_key(existing):
            by_system[candidate.build_system] = candidate
    return sorted(by_system.values(), key=_build_sort_key)


def _build_sort_key(candidate: BuildCandidate) -> tuple[float, str, str]:
    return (-candidate.confidence, candidate.build_system, ",".join(candidate.entrypoint_paths))


def _rank_entrypoints(
    repo_path: Path, files: list[Path], primary_binary: str | None
) -> list[EntrypointCandidate]:
    candidates: list[EntrypointCandidate] = []
    candidates.extend(_python_project_entrypoints(repo_path))
    candidates.extend(_python_module_entrypoints(repo_path, files))
    candidates.extend(_executable_file_entrypoints(repo_path, files))
    candidates.extend(_python_script_entrypoints(repo_path, files))
    return _prioritize_primary_binary(_dedupe_entrypoints(candidates), primary_binary)


def _python_project_entrypoints(repo_path: Path) -> list[EntrypointCandidate]:
    data = _load_pyproject(repo_path / "pyproject.toml")
    scripts: dict[str, str] = {}
    project_scripts = data.get("project", {}).get("scripts", {})
    if isinstance(project_scripts, dict):
        scripts.update({str(name): str(target) for name, target in project_scripts.items()})
    poetry_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
    if isinstance(poetry_scripts, dict):
        scripts.update(
            {
                str(name): str(target)
                for name, target in poetry_scripts.items()
                if isinstance(target, str)
            }
        )

    candidates: list[EntrypointCandidate] = []
    for name, target in sorted(scripts.items()):
        module, _, callable_name = target.partition(":")
        module_path = _module_to_path(repo_path, module)
        if module_path is None:
            continue
        candidates.append(
            EntrypointCandidate(
                name=name,
                path=_rel(repo_path, module_path),
                invocation_kind="python-entrypoint",
                confidence=0.99,
                reason=f"declared project script {name}",
                language="python",
                module=module,
                callable_name=callable_name or None,
            )
        )
    return candidates


def _python_module_entrypoints(repo_path: Path, files: list[Path]) -> list[EntrypointCandidate]:
    candidates: list[EntrypointCandidate] = []
    for path in files:
        if path.name != "__main__.py":
            continue
        if _has_any_part(path.relative_to(repo_path), ENTRYPOINT_SKIP_DIRS):
            continue
        package_dir = path.parent
        if package_dir == repo_path or not (package_dir / "__init__.py").exists():
            continue
        module = ".".join(package_dir.relative_to(repo_path).parts)
        candidates.append(
            EntrypointCandidate(
                name=package_dir.name,
                path=_rel(repo_path, path),
                invocation_kind="python-module",
                confidence=0.88,
                reason="package provides __main__.py",
                language="python",
                module=module,
            )
        )
    return candidates


def _python_script_entrypoints(repo_path: Path, files: list[Path]) -> list[EntrypointCandidate]:
    candidates: list[EntrypointCandidate] = []
    for path in files:
        if path.suffix != ".py" or path.name == "__init__.py":
            continue
        if _has_any_part(path.relative_to(repo_path), ENTRYPOINT_SKIP_DIRS):
            continue
        text = _read_head(path)
        has_main_guard = 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text
        has_python_shebang = _language_from_shebang(path) == "python"
        conventional = path.name in {"app.py", "cli.py", "main.py", "program.py"}
        if not (has_main_guard or has_python_shebang or conventional):
            continue
        confidence = 0.74
        reasons = []
        if has_main_guard:
            confidence += 0.08
            reasons.append("main guard")
        if has_python_shebang:
            confidence += 0.06
            reasons.append("python shebang")
        if conventional:
            confidence += 0.05
            reasons.append("conventional script name")
        if path.parent == repo_path:
            confidence += 0.03
            reasons.append("top-level file")
        candidates.append(
            EntrypointCandidate(
                name=path.stem,
                path=_rel(repo_path, path),
                invocation_kind="python-script",
                confidence=round(min(confidence, 0.93), 3),
                reason=", ".join(reasons),
                language="python",
            )
        )
    return candidates


def _executable_file_entrypoints(repo_path: Path, files: list[Path]) -> list[EntrypointCandidate]:
    candidates: list[EntrypointCandidate] = []
    for path in files:
        if path.name in MAKEFILE_NAMES or path.suffix not in EXECUTABLE_SOURCE_SUFFIXES:
            continue
        if _has_any_part(path.relative_to(repo_path), ENTRYPOINT_SKIP_DIRS):
            continue
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            continue
        language = _language_from_shebang(path)
        if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)) and language is None:
            continue
        confidence = 0.8 if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else 0.74
        reasons = (
            ["executable bit set"]
            if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            else ["shebang script"]
        )
        if path.parent == repo_path:
            confidence += 0.05
            reasons.append("top-level file")
        if path.suffix == "":
            confidence += 0.04
            reasons.append("binary-like name")
        if language == "python":
            confidence += 0.03
            reasons.append("python shebang")
        candidates.append(
            EntrypointCandidate(
                name=path.name,
                path=_rel(repo_path, path),
                invocation_kind="executable-file",
                confidence=round(min(confidence, 0.95), 3),
                reason=", ".join(reasons),
                language=language or "script",
            )
        )
    return candidates


def _detect_dependency_manifests(repo_path: Path, files: list[Path]) -> list[DependencyManifest]:
    manifests: list[DependencyManifest] = []
    for path in files:
        kind = MANIFEST_KINDS.get(path.name)
        if kind is None:
            continue
        ecosystem, manifest_kind = kind
        manifests.append(
            DependencyManifest(path=_rel(repo_path, path), ecosystem=ecosystem, kind=manifest_kind)
        )
    return sorted(manifests, key=lambda manifest: manifest.path)


def _detect_docs(repo_path: Path) -> list[str]:
    docs: list[str] = []
    for child in sorted(repo_path.iterdir(), key=lambda path: path.name.lower()):
        name = child.name.lower()
        if child.is_file() and any(name.startswith(prefix) for prefix in DOC_FILE_PREFIXES):
            docs.append(child.relative_to(repo_path).as_posix())
        elif child.is_dir() and name in DOC_DIR_NAMES:
            docs.append(child.relative_to(repo_path).as_posix())
            for nested in sorted(child.rglob("*"), key=lambda path: path.as_posix().lower()):
                if nested.is_file() and nested.suffix.lower() in {"", ".md", ".rst", ".txt"}:
                    docs.append(nested.relative_to(repo_path).as_posix())
    return sorted(docs)


def _detect_assets(repo_path: Path, docs_paths: tuple[str, ...]) -> list[str]:
    docs_roots = tuple(Path(path) for path in docs_paths)
    manifest_names = set(MANIFEST_KINDS)
    assets: list[str] = []
    for child in _iter_files(repo_path):
        rel = Path(_rel(repo_path, child))
        if _is_under_any(rel, docs_roots):
            continue
        if child.name in manifest_names or child.name.lower().startswith("readme"):
            continue
        if child.suffix.lower() in ASSET_SUFFIXES:
            assets.append(rel.as_posix())
    return sorted(assets)


def _parse_make_output_hints(makefile: Path) -> list[str]:
    hints: list[str] = []
    phony_targets: set[str] = set()
    target_re = re.compile(r"^([A-Za-z0-9_./-][A-Za-z0-9_./ -]*?)\s*:(?![=])")
    for raw_line in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line or line.startswith("\t"):
            continue
        match = target_re.match(line)
        if not match:
            continue
        targets = [target.strip() for target in match.group(1).split() if target.strip()]
        if ".PHONY" in targets:
            phony_targets.update(
                target
                for target in line.split(":", 1)[1].split()
                if target and target not in COMMON_NON_OUTPUT_TARGETS
            )
            continue
        for target in targets:
            if (
                target.startswith(".")
                or target in COMMON_NON_OUTPUT_TARGETS
                or target in phony_targets
                or target.endswith((".o", ".a", ".so", ".dylib"))
            ):
                continue
            hints.append(target)
    return sorted(dict.fromkeys(hints))


def _single_c_family_source(repo_path: Path, files: list[Path]) -> Path | None:
    candidates = [
        path
        for path in files
        if path.suffix.lower() in {".c", ".cpp", ".cc", ".cxx"}
    ]
    if len(candidates) != 1:
        return None
    if any((repo_path / manifest).exists() for manifest in MAKEFILE_NAMES | {"CMakeLists.txt"}):
        return None
    return candidates[0]


def _single_source_compiler_name(language: str) -> str:
    return "c++" if language == "c++" else "cc"


def _infer_make_language(files: list[Path]) -> str | None:
    suffixes = {path.suffix.lower() for path in files}
    if suffixes & {".cpp", ".cc", ".cxx"}:
        return "c++"
    if ".c" in suffixes:
        return "c"
    if ".py" in suffixes:
        return "python"
    if ".go" in suffixes:
        return "go"
    if ".rs" in suffixes:
        return "rust"
    return None


def _load_pyproject(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _module_to_path(repo_path: Path, module: str) -> Path | None:
    if not module or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$", module):
        return None
    module_rel = Path(*module.split("."))
    module_file = repo_path / module_rel.with_suffix(".py")
    if module_file.exists():
        return module_file
    src_module_file = repo_path / "src" / module_rel.with_suffix(".py")
    if src_module_file.exists():
        return src_module_file
    package_init = repo_path / module_rel / "__init__.py"
    if package_init.exists():
        return package_init
    src_package_init = repo_path / "src" / module_rel / "__init__.py"
    if src_package_init.exists():
        return src_package_init
    return None


def _language_from_shebang(path: Path) -> str | None:
    first_line = _read_head(path, limit=256).splitlines()[:1]
    if not first_line or not first_line[0].startswith("#!"):
        return None
    shebang = first_line[0].lower()
    if "python" in shebang:
        return "python"
    if "bash" in shebang or " sh" in shebang:
        return "shell"
    if "node" in shebang:
        return "javascript"
    if "ruby" in shebang:
        return "ruby"
    if "perl" in shebang:
        return "perl"
    return "script"


def _dedupe_entrypoints(candidates: list[EntrypointCandidate]) -> list[EntrypointCandidate]:
    by_identity: dict[tuple[str, str], EntrypointCandidate] = {}
    for candidate in candidates:
        identity = (candidate.path, candidate.invocation_kind)
        existing = by_identity.get(identity)
        if existing is None or _entrypoint_sort_key(candidate) < _entrypoint_sort_key(existing):
            by_identity[identity] = candidate
    return sorted(by_identity.values(), key=_entrypoint_sort_key)


def _prioritize_primary_binary(
    candidates: list[EntrypointCandidate], primary_binary: str | None
) -> list[EntrypointCandidate]:
    if not primary_binary:
        return candidates
    normalized = primary_binary.strip().lstrip("./")
    if not normalized:
        return candidates

    def sort_key(candidate: EntrypointCandidate) -> tuple[int, float, int, str, str]:
        matches = normalized in {candidate.name, candidate.path, Path(candidate.path).name}
        base_key = _entrypoint_sort_key(candidate)
        return (0 if matches else 1, *base_key)

    return sorted(candidates, key=sort_key)


def _entrypoint_sort_key(candidate: EntrypointCandidate) -> tuple[float, int, str, str]:
    return (
        -candidate.confidence,
        _conventional_name_rank(candidate.name, candidate.path),
        candidate.path,
        candidate.invocation_kind,
    )


def _conventional_name_rank(name: str, path: str) -> int:
    lowered = {name.lower(), Path(path).name.lower(), Path(path).stem.lower()}
    if lowered & {"program", "main", "cli"}:
        return 0
    if any(value.startswith("pb") for value in lowered):
        return 1
    return 2


def _iter_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for child in repo_path.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(repo_path)
        if _has_any_part(rel, IGNORED_DIRS):
            continue
        files.append(child)
    return sorted(files, key=lambda path: _rel(repo_path, path))


def _first_existing(repo_path: Path, names: set[str]) -> Path | None:
    for name in sorted(names):
        candidate = repo_path / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _read_head(path: Path, *, limit: int = 50_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _rel(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _has_any_part(path: Path, names: set[str]) -> bool:
    return any(part in names for part in path.parts)


def _is_under_any(path: Path, parents: tuple[Path, ...]) -> bool:
    for parent in parents:
        if path == parent:
            return True
        try:
            path.relative_to(parent)
        except ValueError:
            continue
        return True
    return False
