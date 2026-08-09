"""Compile a generated board port into something flashable.

This is the step that turns the product's output from "files a firmware
engineer could use" into "the image to put on the board". It is also the only
place in the system that produces a *fact* rather than a claim: either the
compiler accepted it or it did not, and the answer is not open to
interpretation.

That makes it the supervision signal the corpus has been missing. Every other
record says what was asked and answered; this one says whether the result was
buildable.

What it does not do
-------------------
It does not flash anything. The image is handed to the person holding the
board, with the commands to write it. Flashing is destructive to whatever was
on the part, it needs the right programmer and the right connection, and both
are things only the person in the room can confirm. A server that flashed on
the user's behalf would be guessing about a physical connection it cannot see.

It also does not make the firmware correct. A build proves the devicetree is
consistent and the drivers compile against it. Whether the DHT22 is really on
P0.13 is still exactly as good as the answer that said so.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import FWAgentError


class BuildUnavailable(FWAgentError):
    """The toolchain needed to build is not present, with what is missing."""


@dataclass
class BuildResult:
    """What came out, and what it does and does not establish."""

    ok: bool
    log: str
    artifacts: dict[str, bytes] = field(default_factory=dict)
    """Flashable images, keyed by filename: zephyr.hex, zephyr.bin, zephyr.elf."""
    flash_used: int | None = None
    flash_total: int | None = None
    ram_used: int | None = None
    ram_total: int | None = None
    board: str = ""

    @property
    def summary(self) -> str:
        if not self.ok:
            return "the build failed"
        parts = []
        if self.flash_used and self.flash_total:
            parts.append(
                f"flash {self.flash_used} B of {self.flash_total} "
                f"({100 * self.flash_used / self.flash_total:.2f} %)"
            )
        if self.ram_used and self.ram_total:
            parts.append(
                f"RAM {self.ram_used} B of {self.ram_total} "
                f"({100 * self.ram_used / self.ram_total:.2f} %)"
            )
        return "built: " + ", ".join(parts) if parts else "built"


def _parse_memory(log: str) -> dict[str, int]:
    """Read the linker's own memory report rather than measuring the file.

    A .bin's size is not the flash footprint and a .hex's is not either; the
    linker knows, and it prints it.
    """
    sizes: dict[str, int] = {}
    units = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 ** 3}

    for line in log.splitlines():
        parts = line.split()
        # e.g. "FLASH:  34288 B   1 MB   3.27%"
        if len(parts) >= 5 and parts[0].rstrip(":").upper() in {"FLASH", "RAM"}:
            region = parts[0].rstrip(":").lower()
            try:
                used = int(parts[1]) * units.get(parts[2], 1)
                total = int(parts[3]) * units.get(parts[4], 1)
            except (ValueError, IndexError):
                continue
            sizes[f"{region}_used"] = used
            sizes[f"{region}_total"] = total
    return sizes


class ZephyrBuilder:
    """Runs `west build` over a generated board port."""

    #: Produced by every Zephyr build; the first two are what people flash.
    ARTIFACTS = ("zephyr.hex", "zephyr.bin", "zephyr.elf")

    def __init__(
        self,
        zephyr_base: Path | str | None = None,
        west: str | None = None,
        timeout: int = 900,
    ) -> None:
        base = zephyr_base or os.environ.get("ZEPHYR_BASE")
        self._base = Path(base) if base else None
        self._west = west or shutil.which("west") or ""
        self._timeout = timeout

    def missing(self) -> list[str]:
        """Everything absent that a build needs, so the reason is one message.

        Reported all at once rather than failing on the first: someone setting
        this up wants the whole list, not three rounds of it.
        """
        gaps: list[str] = []
        if self._base is None or not (self._base / "dts").is_dir():
            gaps.append(
                "no Zephyr checkout (set ZEPHYR_BASE to a west workspace's "
                "zephyr/ directory)"
            )
        if not self._west:
            gaps.append("`west` is not on PATH (pip install west)")
        if not shutil.which("cmake"):
            gaps.append("`cmake` is not on PATH (pip install cmake)")
        if not shutil.which("ninja"):
            gaps.append("`ninja` is not on PATH (pip install ninja)")
        if not self._sdk_present():
            gaps.append(
                "no Zephyr SDK toolchain found (west sdk install --toolchains "
                "arm-zephyr-eabi)"
            )
        return gaps

    @staticmethod
    def _sdk_present() -> bool:
        if os.environ.get("ZEPHYR_SDK_INSTALL_DIR"):
            return True
        return any(Path.home().glob("zephyr-sdk-*"))

    @property
    def available(self) -> bool:
        return not self.missing()

    def build(self, files: dict[str, str], board: str) -> BuildResult:
        """Write the port to a scratch tree and compile it.

        The port is built in a temporary directory rather than in the
        workspace: a build that leaves state behind makes the next one depend
        on the last, and 'works the second time' is not a property worth
        shipping.
        """
        gaps = self.missing()
        if gaps:
            raise BuildUnavailable(
                "this instance cannot compile firmware. Missing:\n"
                + "\n".join(f"  - {gap}" for gap in gaps)
            )

        with tempfile.TemporaryDirectory(prefix="fwagent-build-") as scratch:
            root = Path(scratch)
            for relative, content in files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            build_dir = root / "build"
            command = [
                self._west, "build",
                "-p", "always",
                "-b", board,
                str(root / "app"),
                "-d", str(build_dir),
                "--", f"-DBOARD_ROOT={root.as_posix()}",
            ]

            environment = {**os.environ, "ZEPHYR_BASE": str(self._base)}
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True,
                    timeout=self._timeout, env=environment, cwd=scratch,
                )
            except subprocess.TimeoutExpired:
                return BuildResult(
                    ok=False, board=board,
                    log=f"the build did not finish within {self._timeout} s and was stopped",
                )
            except OSError as exc:
                return BuildResult(ok=False, board=board, log=f"could not run west: {exc}")

            log = (completed.stdout + completed.stderr).replace("\r\n", "\n")
            ok = completed.returncode == 0

            artifacts: dict[str, bytes] = {}
            for name in self.ARTIFACTS:
                path = build_dir / "zephyr" / name
                if path.is_file():
                    artifacts[name] = path.read_bytes()

            if ok and not artifacts:
                # west returned success and produced nothing to flash. Reported
                # as a failure: an empty result presented as a build is worse
                # than an error.
                ok = False
                log += "\n\nthe build reported success but produced no image"

            sizes = _parse_memory(log)
            return BuildResult(
                ok=ok, log=log, artifacts=artifacts, board=board,
                flash_used=sizes.get("flash_used"), flash_total=sizes.get("flash_total"),
                ram_used=sizes.get("ram_used"), ram_total=sizes.get("ram_total"),
            )


def flashing_instructions(board: str, result: BuildResult) -> str:
    """What the person holding the board has to do, and what it costs them."""
    return f"""# Flashing this firmware

`zephyr.hex` and `zephyr.bin` are the images. Use whichever your programmer
wants -- most take the hex, which carries its own load addresses.

{result.summary}.

## Before you connect anything

Writing this replaces whatever is on the part, including any bootloader that
is not in a protected region. It is recoverable on most parts and not on all.

## With a debug probe

```sh
west flash --build-dir <dir>            # if this board has a runner configured
```

The generated `board.cmake` does not configure one unless a programmer was
stated, because flashing the wrong way can leave a part unreachable. Add the
one you use:

```cmake
include(${{ZEPHYR_BASE}}/boards/common/openocd.board.cmake)   # or jlink, nrfjprog, pyocd
```

Or drive the tool directly:

```sh
pyocd flash -t <target> zephyr.hex
JLinkExe -device <device> -if SWD -speed 4000 -CommanderScript flash.jlink
nrfjprog --program zephyr.hex --chiperase --verify --reset
openocd -f interface/<probe>.cfg -f target/<target>.cfg \\
        -c "program zephyr.hex verify reset exit"
```

## What a successful flash proves

That the image is on the part. Nothing more.

The build establishes that the devicetree is consistent and the drivers compile
against it. It says nothing about whether the pins in it match the board. If a
sensor was declared on the wrong pin, this firmware runs, reports no fault, and
reads a floating input -- see PROVENANCE.md for the exact list of values that
came from answers rather than from an artifact. Those are the ones to check
first when a reading looks wrong.

Watch the console on the UART named in PROVENANCE.md at the stated baud rate.
A device that fails `device_is_ready()` says so by name on that line.
"""
