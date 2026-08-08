import pytest

from core.exceptions import CompilationError, ToolchainNotFoundError
from services.toolchain import AvrToolchain, find_executable

# These tests invoke the real compiler. They skip (rather than fake a pass)
# on machines where the toolchain is not installed.
requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(),
    reason="avr-gcc is not installed on this machine",
)

VALID_BLINK = """
#include <avr/io.h>

int main(void) {
    DDRB |= (1 << PB5);
    while (1) {
        PORTB ^= (1 << PB5);
    }
    return 0;
}
"""

BROKEN_SOURCE = """
int main(void) {
    this is not valid c;
}
"""


def test_find_executable_returns_none_for_nonexistent_tool():
    assert find_executable("definitely-not-a-real-compiler-xyz") is None


def test_constructing_toolchain_with_bad_path_raises_with_install_hint():
    with pytest.raises(ToolchainNotFoundError, match="winget install"):
        AvrToolchain(gcc_path="/nonexistent/avr-gcc")


@requires_avr
def test_toolchain_reports_its_real_version():
    version = AvrToolchain().version()

    assert "gcc" in version.lower()


@requires_avr
def test_valid_source_passes_syntax_check(tmp_path):
    source = tmp_path / "blink.c"
    source.write_text(VALID_BLINK, encoding="utf-8")

    result = AvrToolchain().check_syntax(source, mcu="atmega328p", f_cpu_hz=16_000_000)

    assert result.ok, result.diagnostics
    assert result.returncode == 0


@requires_avr
def test_broken_source_fails_syntax_check_with_diagnostics(tmp_path):
    source = tmp_path / "broken.c"
    source.write_text(BROKEN_SOURCE, encoding="utf-8")

    result = AvrToolchain().check_syntax(source, mcu="atmega328p")

    assert not result.ok
    assert result.returncode != 0
    assert "error" in result.diagnostics.lower()


@requires_avr
def test_compile_to_elf_produces_a_real_binary(tmp_path):
    source = tmp_path / "blink.c"
    source.write_text(VALID_BLINK, encoding="utf-8")
    output = tmp_path / "blink.elf"

    result = AvrToolchain().compile_to_elf(
        [source], output, mcu="atmega328p", f_cpu_hz=16_000_000
    )

    assert result.ok, result.diagnostics
    assert output.is_file()
    assert output.stat().st_size > 0


@requires_avr
def test_missing_source_file_raises_compilation_error():
    with pytest.raises(CompilationError, match="does not exist"):
        AvrToolchain().check_syntax("/no/such/file.c", mcu="atmega328p")


@requires_avr
def test_compile_with_no_sources_raises_compilation_error(tmp_path):
    with pytest.raises(CompilationError, match="no source files"):
        AvrToolchain().compile_to_elf([], tmp_path / "out.elf", mcu="atmega328p")
