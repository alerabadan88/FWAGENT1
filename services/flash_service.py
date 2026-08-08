"""Write firmware to a physical AVR board with avrdude.

This is the only module here that changes something outside the machine, and
it is deliberately conservative about it:

* **The port is never guessed.** Flashing the wrong device is unrecoverable
  from software's point of view, so a port must be named explicitly. Port
  discovery exists to *show* the user their options, not to pick one.
* **Dry run is a first-class mode** (``avrdude -n``): it talks to the board and
  reports what would happen without writing.
* **Verification is on by default.** avrdude reads the flash back and compares;
  disabling that is opt-in.
* A flash that did not run reports ``status="not_run"`` — never success.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import FWAgentError
from core.hardware_model import MCU
from services.toolchain import AvrToolchain

AVRDUDE_INSTALL_HINT = (
    "avrdude was not found. Install it with:\n"
    "  Windows: winget install --id ZakKemble.avr-gcc (bundles avrdude)\n"
    "  Debian/Ubuntu: sudo apt install avrdude\n"
    "  macOS: brew install avrdude"
)

# Programmer to use for each board, keyed by the MCU part. The Uno's bootloader
# speaks STK500v1 ("arduino") at 115200; a bare chip on an ISP needs a different
# programmer entirely, which is why this is not a single default.
_BOARD_PROGRAMMERS = {
    "ATMEGA328P": {"programmer": "arduino", "baud": 115200, "part": "m328p"},
    "ATMEGA168": {"programmer": "arduino", "baud": 19200, "part": "m168"},
    "ATMEGA2560": {"programmer": "wiring", "baud": 115200, "part": "m2560"},
}


class FlashError(FWAgentError):
    """Raised when firmware cannot be written to a device."""


@dataclass
class SerialPort:
    name: str
    description: str = ""

    def __str__(self) -> str:
        return f"{self.name} - {self.description}" if self.description else self.name


@dataclass
class FlashResult:
    """Outcome of one flash attempt.

    ``status`` is ``success``, ``failed``, or ``not_run``.
    """

    status: str
    port: str
    dry_run: bool = False
    bytes_written: int | None = None
    verified: bool = False
    diagnostics: str = ""
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"

    @property
    def error_line(self) -> str:
        """The most informative failure line avrdude printed.

        avrdude always signs off with "Avrdude done. Thank you." even on
        failure, so the last line is exactly the wrong one to surface.
        """
        errors = [
            line.strip()
            for line in self.diagnostics.splitlines()
            if line.strip().lower().startswith(("error", "avrdude: error"))
        ]
        if errors:
            return errors[0]
        meaningful = [
            line.strip()
            for line in self.diagnostics.splitlines()
            if line.strip() and "thank you" not in line.lower()
        ]
        return meaningful[-1] if meaningful else "unknown error"

    def summary(self) -> str:
        if self.status == "not_run":
            return f"not run: {self.diagnostics}"
        if self.status == "failed":
            return f"failed on {self.port}: {self.error_line}"
        what = "would write" if self.dry_run else "wrote"
        size = f"{self.bytes_written} bytes" if self.bytes_written is not None else "firmware"
        verified = ", verified" if self.verified else ""
        return f"{what} {size} to {self.port}{verified}"


def find_avrdude() -> Path | None:
    """Locate avrdude on PATH, then next to avr-gcc (they ship together)."""
    found = shutil.which("avrdude")
    if found:
        return Path(found)

    try:
        toolchain = AvrToolchain()
    except FWAgentError:
        return None

    suffix = ".exe" if os.name == "nt" else ""
    candidate = toolchain.gcc_path.parent / f"avrdude{suffix}"
    return candidate if candidate.is_file() else None


def list_serial_ports() -> list[SerialPort]:
    """Enumerate serial ports so the user can pick one. Never picks for them."""
    if os.name == "nt":
        return _list_ports_windows()
    return _list_ports_posix()


def _list_ports_windows() -> list[SerialPort]:
    script = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.Name -match '\\(COM\\d+\\)' } | "
        "ForEach-Object { $_.Name }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    ports = []
    for line in completed.stdout.splitlines():
        match = re.search(r"\(?(COM\d+)\)?", line)
        if match:
            description = line.replace(match.group(0), "").strip(" -")
            ports.append(SerialPort(name=match.group(1), description=description))
    return ports


def _list_ports_posix() -> list[SerialPort]:
    ports = []
    for pattern in ("ttyUSB", "ttyACM", "cu.usbserial", "cu.usbmodem"):
        for path in sorted(Path("/dev").glob(f"{pattern}*")):
            ports.append(SerialPort(name=str(path)))
    return ports


class FlashService:
    """Writes a HEX image to a board over a serial bootloader."""

    def __init__(self, avrdude_path: str | Path | None = None) -> None:
        resolved = Path(avrdude_path) if avrdude_path else find_avrdude()
        if resolved is None or not Path(resolved).is_file():
            raise FlashError(AVRDUDE_INSTALL_HINT)
        self.avrdude_path = Path(resolved)

    @classmethod
    def is_available(cls) -> bool:
        return find_avrdude() is not None

    @staticmethod
    def programmer_for(mcu: MCU) -> dict[str, object]:
        settings = _BOARD_PROGRAMMERS.get(mcu.name.upper())
        if settings is None:
            raise FlashError(
                f"no avrdude programmer is configured for '{mcu.name}' "
                f"(known: {sorted(_BOARD_PROGRAMMERS)})"
            )
        return settings

    def version(self) -> str:
        result = self._run([str(self.avrdude_path), "-v"])
        # avrdude prints its banner on stderr.
        text = (result.stderr or result.stdout).strip()
        for line in text.splitlines():
            if "avrdude version" in line.lower():
                return line.strip()
        return text.splitlines()[0].strip() if text else "unknown"

    def flash(
        self,
        hex_path: str | Path,
        mcu: MCU,
        port: str,
        dry_run: bool = False,
        verify: bool = True,
        timeout: int = 120,
    ) -> FlashResult:
        """Write ``hex_path`` to the board on ``port``.

        ``port`` is required and never inferred — see the module docstring.
        """
        hex_path = Path(hex_path)
        if not hex_path.is_file():
            return FlashResult(
                status="not_run",
                port=port,
                dry_run=dry_run,
                diagnostics=f"HEX file does not exist: {hex_path}",
            )
        if not port or not port.strip():
            return FlashResult(
                status="not_run",
                port=port,
                dry_run=dry_run,
                diagnostics="a serial port must be given explicitly",
            )

        settings = self.programmer_for(mcu)

        command = [
            str(self.avrdude_path),
            "-p", str(settings["part"]),
            "-c", str(settings["programmer"]),
            "-P", port,
            "-b", str(settings["baud"]),
            "-D",  # don't chip-erase the whole flash; the bootloader lives there
            "-U", f"flash:w:{hex_path}:i",
        ]
        if dry_run:
            command.append("-n")
        if not verify:
            command.append("-V")

        result = self._run(command, timeout=timeout)
        output = f"{result.stdout}\n{result.stderr}".strip()

        if not result.ok:
            return FlashResult(
                status="failed",
                port=port,
                dry_run=dry_run,
                diagnostics=output,
                command=command,
            )

        return FlashResult(
            status="success",
            port=port,
            dry_run=dry_run,
            bytes_written=self._parse_bytes_written(output),
            verified=verify and "verified" in output.lower(),
            diagnostics=output,
            command=command,
        )

    @staticmethod
    def _parse_bytes_written(output: str) -> int | None:
        """Read the byte count out of avrdude's 'writing N bytes' progress line."""
        match = re.search(r"(\d+)\s+bytes? of flash (?:written|verified)", output, re.I)
        if match:
            return int(match.group(1))
        match = re.search(r"writing\s+(\d+)\s+bytes", output, re.I)
        return int(match.group(1)) if match else None

    @staticmethod
    def _run(command: list[str], timeout: int = 120):
        from services.toolchain import CompileResult

        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise FlashError(
                f"avrdude timed out after {timeout}s — is the board in bootloader mode?"
            ) from exc
        except OSError as exc:
            raise FlashError(f"could not execute {command[0]}: {exc}") from exc

        return CompileResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
