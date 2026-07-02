"""Language adapter interfaces and registry.

The adapters here describe what the current implementation can safely do for a
language/build-system pair. They intentionally delegate to the existing local
builder until the later compiled-language phases harden those paths.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pbgen.config import PBGenConfig
from pbgen.errors import BuildError
from pbgen.schemas import LanguageCapabilityReport, TaskSpec

if TYPE_CHECKING:
    from pbgen.build.build_agent import BuildBackend


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower() or None


def _normalize_build_system(value: str | None) -> str | None:
    normalized = _normalize(value)
    return None if normalized == "auto" else normalized


class LanguageAdapter(ABC):
    """Build, probe, coverage, and rendering capabilities for one language family."""

    name: str
    languages: frozenset[str]
    build_systems: frozenset[str]

    def matches(self, language: str | None, build_system: str | None) -> bool:
        normalized_language = _normalize(language)
        normalized_build = _normalize_build_system(build_system)
        language_matches = normalized_language in self.languages if normalized_language else False
        build_matches = normalized_build in self.build_systems if normalized_build else False
        return language_matches or build_matches

    @abstractmethod
    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        """Return a structured report for the requested capability pair."""

    @abstractmethod
    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        """Return a build backend appropriate for this adapter."""


class PythonLanguageAdapter(LanguageAdapter):
    """Python package and script adapter."""

    name = "python"
    languages = frozenset({"python"})
    build_systems = frozenset(
        {
            "script",
            "python-script",
            "python-package",
            "project",
            "requirements",
            "setuptools",
            "dependency-lock",
            "custom-command",
        }
    )

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        normalized_build = _normalize_build_system(build_system)
        build_supported = normalized_build in self.build_systems if normalized_build else True
        return LanguageCapabilityReport(
            language=language,
            build_system=build_system,
            adapter_name=self.name,
            supported=build_supported,
            build_supported=build_supported,
            coverage_supported=True,
            behavior_probe_supported=True,
            test_rendering_supported=True,
            package_runtime="python3",
            reason=None if build_supported else f"Build system is not supported yet: {build_system}",
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        report = self.capability_report(language=None, build_system=build_system_override)
        if not report.build_supported:
            raise BuildError(report.reason or "Python adapter cannot build this task.")
        from pbgen.build.build_agent import LocalBuildBackend

        return LocalBuildBackend(
            build_system_override=build_system_override,
            build_timeout_seconds=config.build_timeout_seconds,
            probe_timeout_seconds=config.probe_timeout_seconds,
            allow_custom_build_command=config.allow_custom_build_command,
            execution_policy=config.execution_policy,
            safe_command_allow_patterns=config.safe_command_allow_patterns,
            safe_command_deny_patterns=config.safe_command_deny_patterns,
            trusted_local_execution=config.trusted_local_execution,
        )


class CLanguageAdapter(LanguageAdapter):
    """C/C++ adapter for the current Make and single-file C build paths."""

    name = "c-cpp"
    languages = frozenset({"c", "cpp", "c++", "c/c++", "make"})
    build_systems = frozenset({"make", "cmake", "c-single", "custom-command"})

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        normalized_build = _normalize_build_system(build_system)
        build_supported = normalized_build in self.build_systems if normalized_build else True
        return LanguageCapabilityReport(
            language=language,
            build_system=build_system,
            adapter_name=self.name,
            supported=build_supported,
            build_supported=build_supported,
            coverage_supported=True,
            behavior_probe_supported=True,
            test_rendering_supported=True,
            package_runtime="native executable",
            reason=None if build_supported else f"Build system is not supported yet: {build_system}",
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        report = self.capability_report(language=None, build_system=build_system_override)
        if not report.build_supported:
            raise BuildError(report.reason or "C/C++ adapter cannot build this task.")
        from pbgen.build.build_agent import LocalBuildBackend

        return LocalBuildBackend(
            build_system_override=build_system_override,
            build_timeout_seconds=config.build_timeout_seconds,
            probe_timeout_seconds=config.probe_timeout_seconds,
            allow_custom_build_command=config.allow_custom_build_command,
            execution_policy=config.execution_policy,
            safe_command_allow_patterns=config.safe_command_allow_patterns,
            safe_command_deny_patterns=config.safe_command_deny_patterns,
            trusted_local_execution=config.trusted_local_execution,
        )


class GoLanguageAdapter(LanguageAdapter):
    """Go adapter skeleton for conservative `go build` support."""

    name = "go"
    languages = frozenset({"go"})
    build_systems = frozenset({"go"})

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        return _managed_language_report(
            adapter_name=self.name,
            language=language,
            build_system=build_system,
            build_systems=self.build_systems,
            package_runtime="native executable",
            coverage_reason="Go coverage is not implemented yet.",
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        return _local_backend(config, build_system_override)


class RustLanguageAdapter(LanguageAdapter):
    """Rust adapter skeleton for conservative Cargo build support."""

    name = "rust"
    languages = frozenset({"rust"})
    build_systems = frozenset({"cargo"})

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        return _managed_language_report(
            adapter_name=self.name,
            language=language,
            build_system=build_system,
            build_systems=self.build_systems,
            package_runtime="native executable",
            coverage_reason="Rust coverage is not implemented yet.",
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        return _local_backend(config, build_system_override)


class JavaLanguageAdapter(LanguageAdapter):
    """Java adapter skeleton for Maven and Gradle package builds."""

    name = "java"
    languages = frozenset({"java"})
    build_systems = frozenset({"maven", "gradle"})

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        return _managed_language_report(
            adapter_name=self.name,
            language=language,
            build_system=build_system,
            build_systems=self.build_systems,
            package_runtime="java",
            coverage_reason="Java coverage is not implemented yet.",
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        return _local_backend(config, build_system_override)


class UnsupportedLanguageAdapter(LanguageAdapter):
    """Explicit unsupported-language adapter used for diagnostics."""

    name = "unsupported"
    languages = frozenset[str]()
    build_systems = frozenset[str]()

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or "No language adapter supports this language/build-system pair."

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        return LanguageCapabilityReport(
            language=language,
            build_system=build_system,
            adapter_name=self.name,
            supported=False,
            build_supported=False,
            coverage_supported=False,
            behavior_probe_supported=False,
            test_rendering_supported=False,
            reason=self._reason,
        )

    def build_backend(
        self,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        del config, build_system_override
        raise BuildError(self._reason)


class LanguageAdapterRegistry:
    """Deterministic language adapter selection."""

    def __init__(self, adapters: list[LanguageAdapter] | None = None) -> None:
        self.adapters = adapters or [
            PythonLanguageAdapter(),
            CLanguageAdapter(),
            GoLanguageAdapter(),
            RustLanguageAdapter(),
            JavaLanguageAdapter(),
        ]

    def select(self, *, language: str | None, build_system: str | None) -> LanguageAdapter:
        normalized_build = _normalize_build_system(build_system)
        for adapter in self.adapters:
            if adapter.matches(language, normalized_build):
                return adapter
        return UnsupportedLanguageAdapter()

    def capability_report(
        self,
        *,
        language: str | None,
        build_system: str | None,
    ) -> LanguageCapabilityReport:
        adapter = self.select(language=language, build_system=build_system)
        normalized_build = None if _normalize_build_system(build_system) is None else build_system
        return adapter.capability_report(language=language, build_system=normalized_build)

    def build_backend(
        self,
        spec: TaskSpec,
        config: PBGenConfig,
        *,
        build_system_override: str | None,
    ) -> "BuildBackend":
        selected_build_system = _select_build_system(spec, build_system_override)
        adapter = self.select(language=spec.language, build_system=selected_build_system)
        report = adapter.capability_report(
            language=spec.language,
            build_system=selected_build_system,
        )
        if not report.build_supported and selected_build_system is not None:
            raise BuildError(report.reason or "Selected language adapter cannot build this task.")
        if not report.build_supported:
            from pbgen.build.build_agent import LocalBuildBackend

            return LocalBuildBackend(
                build_system_override=build_system_override,
                build_timeout_seconds=config.build_timeout_seconds,
                probe_timeout_seconds=config.probe_timeout_seconds,
                allow_custom_build_command=config.allow_custom_build_command,
                execution_policy=config.execution_policy,
                safe_command_allow_patterns=config.safe_command_allow_patterns,
                safe_command_deny_patterns=config.safe_command_deny_patterns,
                trusted_local_execution=config.trusted_local_execution,
            )
        return adapter.build_backend(
            config,
            build_system_override=build_system_override,
        )


def default_language_registry() -> LanguageAdapterRegistry:
    """Return the production language adapter registry."""

    return LanguageAdapterRegistry()


def _select_build_system(spec: TaskSpec, build_system_override: str | None) -> str | None:
    normalized_override = _normalize_build_system(build_system_override)
    if normalized_override:
        return build_system_override
    return spec.build_system


def _managed_language_report(
    *,
    adapter_name: str,
    language: str | None,
    build_system: str | None,
    build_systems: frozenset[str],
    package_runtime: str,
    coverage_reason: str,
) -> LanguageCapabilityReport:
    normalized_build = _normalize_build_system(build_system)
    build_supported = normalized_build in build_systems if normalized_build else True
    warnings = [coverage_reason]
    return LanguageCapabilityReport(
        language=language,
        build_system=build_system,
        adapter_name=adapter_name,
        supported=build_supported,
        build_supported=build_supported,
        coverage_supported=False,
        behavior_probe_supported=True,
        test_rendering_supported=True,
        package_runtime=package_runtime,
        reason=None if build_supported else f"Build system is not supported yet: {build_system}",
        warnings=warnings,
    )


def _local_backend(config: PBGenConfig, build_system_override: str | None) -> "BuildBackend":
    from pbgen.build.build_agent import LocalBuildBackend

    return LocalBuildBackend(
        build_system_override=build_system_override,
        build_timeout_seconds=config.build_timeout_seconds,
        probe_timeout_seconds=config.probe_timeout_seconds,
        allow_custom_build_command=config.allow_custom_build_command,
        execution_policy=config.execution_policy,
        safe_command_allow_patterns=config.safe_command_allow_patterns,
        safe_command_deny_patterns=config.safe_command_deny_patterns,
        trusted_local_execution=config.trusted_local_execution,
    )
