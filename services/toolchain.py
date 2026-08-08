"""Detection of, and real invocation of, C compiler toolchains.

Nothing here simulates compilation. If the toolchain is absent, every entry
point raises :class:`ToolchainNotFoundError` with instructions for installing
it — it never reports success for work it did not do.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import CompilationError, ToolchainNotFoundError

AVR_INSTALL_HINT = (
    "avr-gcc was not found. Install it with:\n"
    "  Windows: winget install --id ZakKemble.avr-gcc\n"
    "  Debian/Ubuntu: sudo apt install gcc-avr avr-libc\n"
    "  macOS: brew tap osx-cross/avr && brew install avr-gcc\n"
    "If it is installed, make sure its bin/ directory is on PATH "
    "(a freshly opened shell may be required)."
)

# winget installs to a versioned directory, so the version is globbed.
_FALLBACK_GLOBS = (
    os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\ZakKemble.avr-gcc_*"
        r"\avr-gcc-*-windows\bin"
    ),
    r"C:\avr-gcc\bin",
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
)


@dataclass(frozen=True)
class CompileResult:
    """Outcome of one real compiler invocation."""

    ok: bool
    returncode: int
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def diagnostics(self) -> str:
        return (self.stderr or self.stdout).strip()


def find_executable(name: str) -> Path | None:
    """Locate a compiler binary on PATH, falling back to known install dirs.

    The fallback matters on Windows: winget adds the toolchain to the
    persistent user PATH, but already-running shells keep the old environment.
    """
    found = shutil.which(name)
    if found:
        return Path(found)

    exe_suffix = ".exe" if os.name == "nt" else ""
    for pattern in _FALLBACK_GLOBS:
        for directory in glob.glob(pattern):
            candidate = Path(directory) / f"{name}{exe_suffix}"
            if candidate.is_file():
                return candidate

    return None


class AvrToolchain:
    """Wrapper around a real ``avr-gcc`` installation."""

    executable_name = "avr-gcc"
    install_hint = AVR_INSTALL_HINT

    def __init__(self, gcc_path: str | Path | None = None) -> None:
        resolved = Path(gcc_path) if gcc_path else find_executable(self.executable_name)
        if resolved is None or not Path(resolved).is_file():
            raise ToolchainNotFoundError(self.install_hint)
        self.gcc_path = Path(resolved)

    @classmethod
    def find(cls) -> AvrToolchain | None:
        """Return a toolchain if one is installed, else ``None``."""
        try:
            return cls()
        except ToolchainNotFoundError:
            return None

    @classmethod
    def is_available(cls) -> bool:
        return find_executable(cls.executable_name) is not None

    @property
    def size_path(self) -> Path:
        """Path to ``avr-size``, which ships alongside ``avr-gcc``."""
        suffix = ".exe" if os.name == "nt" else ""
        candidate = self.gcc_path.parent / f"avr-size{suffix}"
        if not candidate.is_file():
            raise ToolchainNotFoundError(
                f"avr-size was not found next to {self.gcc_path}. {self.install_hint}"
            )
        return candidate

    def section_sizes(self, elf: str | Path) -> dict[str, int]:
        """Read real ``.text``/``.data``/``.bss`` sizes out of a built ELF."""
        elf = Path(elf)
        if not elf.is_file():
            raise CompilationError(f"ELF file does not exist: {elf}")

        result = self._run([str(self.size_path), str(elf)])
        if not result.ok:
            raise CompilationError(f"avr-size failed: {result.diagnostics}")

        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            raise CompilationError(f"could not parse avr-size output: {result.stdout!r}")

        fields = lines[1].split()
        try:
            text, data, bss = (int(fields[i]) for i in range(3))
        except (IndexError, ValueError) as exc:
            raise CompilationError(
                f"could not parse avr-size output line: {lines[1]!r}"
            ) from exc

        return {"text": text, "data": data, "bss": bss}

    def version(self) -> str:
        result = self._run([str(self.gcc_path), "--version"])
        if not result.ok:
            raise CompilationError(f"could not query avr-gcc version: {result.diagnostics}")
        return result.stdout.splitlines()[0].strip()

    def check_syntax(
        self,
        source: str | Path,
        mcu: str,
        f_cpu_hz: int | None = None,
        include_dirs: tuple[str | Path, ...] = (),
        optimization: str = "-Os",
        extra_flags: tuple[str, ...] = (),
    ) -> CompileResult:
        """Run ``-fsyntax-only`` over a real source file.

        Returns a :class:`CompileResult`; a rejected source is a normal result
        with ``ok=False``, not an exception.

        Optimization is on by default because avr-libc's ``<util/delay.h>``
        warns (correctly) that its delay functions do not work when compiled
        at ``-O0``, which would make every syntax check of delay-using code
        noisy for a reason unrelated to the code's validity.
        """
        source = Path(source)
        if not source.is_file():
            raise CompilationError(f"source file does not exist: {source}")

        command = [str(self.gcc_path), f"-mmcu={mcu}", optimization]
        if f_cpu_hz is not None:
            command.append(f"-DF_CPU={f_cpu_hz}UL")
        command.extend(f"-I{Path(d)}" for d in include_dirs)
        command.extend(extra_flags)
        command.extend(["-fsyntax-only", str(source)])

        return self._run(command)

    def compile_to_elf(
        self,
        sources: list[str | Path],
        output: str | Path,
        mcu: str,
        f_cpu_hz: int | None = None,
        include_dirs: tuple[str | Path, ...] = (),
        optimization: str = "-Os",
    ) -> CompileResult:
        """Compile and link real sources into an ELF binary."""
        if not sources:
            raise CompilationError("no source files given to compile_to_elf()")

        for source in sources:
            if not Path(source).is_file():
                raise CompilationError(f"source file does not exist: {source}")

        command = [str(self.gcc_path), f"-mmcu={mcu}", optimization]
        if f_cpu_hz is not None:
            command.append(f"-DF_CPU={f_cpu_hz}UL")
        command.extend(f"-I{Path(d)}" for d in include_dirs)
        command.extend(["-o", str(output)])
        command.extend(str(s) for s in sources)

        return self._run(command)

    @staticmethod
    def _run(command: list[str]) -> CompileResult:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompilationError(f"toolchain timed out after 120s: {' '.join(command)}") from exc
        except OSError as exc:
            raise CompilationError(f"could not execute {command[0]}: {exc}") from exc

        return CompileResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
