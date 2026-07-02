"""Custom exceptions used across the ProgramBench generator."""


class PBGenError(Exception):
    """Base error for expected framework failures."""


class BuildError(PBGenError):
    """Raised when a gold executable cannot be built."""


class CoverageError(PBGenError):
    """Raised when coverage collection or gap analysis fails."""


class TestGenerationError(PBGenError):
    """Raised when generated tests cannot be produced or validated."""


class QualityGateError(PBGenError):
    """Raised when a test suite fails a quality gate."""


class CleanroomPackagingError(PBGenError):
    """Raised when cleanroom packaging or leak checking fails."""
