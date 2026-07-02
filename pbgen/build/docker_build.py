"""Docker build backend placeholder.

The MVP uses local builds. This module exists to keep the build interface ready
for containerized ProgramBench-style reproducibility work.
"""

from __future__ import annotations

from pathlib import Path

from pbgen.build.build_agent import BuildBackend
from pbgen.errors import BuildError
from pbgen.schemas import BuildArtifact, TaskSpec


class DockerBuildBackend(BuildBackend):
    """Placeholder backend for future Docker-based gold builds."""

    def build(self, spec: TaskSpec, repo_path: Path, output_dir: Path) -> BuildArtifact:
        raise BuildError("Docker builds are not implemented in the MVP; use local builds.")
