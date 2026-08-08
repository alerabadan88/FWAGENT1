from core.exceptions import FWAgentError, HardwareValidationError


def test_hardware_validation_error_is_a_fw_agent_error():
    assert issubclass(HardwareValidationError, FWAgentError)
    assert issubclass(FWAgentError, Exception)
