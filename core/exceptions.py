class FWAgentError(Exception):
    """Base exception for all fw-automation-agent errors."""


class HardwareValidationError(FWAgentError):
    """Raised when a hardware configuration (MCU/sensor graph) is structurally invalid."""


class EDAParseError(FWAgentError):
    """Raised when an EDA/config input file cannot be parsed into a PCBAnalysis."""


class CatalogError(FWAgentError):
    """Raised for invalid catalog operations (duplicate part, missing part, bad DB)."""


class ToolchainNotFoundError(FWAgentError):
    """Raised when a required compiler toolchain is not installed on this machine."""


class CompilationError(FWAgentError):
    """Raised when the toolchain runs but rejects the source it was given."""


class CodegenError(FWAgentError):
    """Raised when firmware source cannot be generated for the given hardware."""
