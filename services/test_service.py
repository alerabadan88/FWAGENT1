"""Run generated firmware logic on a simulated ATmega328P and check results.

This is the gate past "it compiles". Each check is a C expression evaluated by
the *real* firmware code, built by the *real* compiler, executing on an
instruction-set simulator of the target part -- so target integer widths,
promotion rules, and overflow behave as they will on the device, not as they
would on a 64-bit host.

The harness replaces ``main.c``, calls the pure entry points the drivers expose,
stores each result in an array, and halts. ``avr-gdb`` reads the array back.
Nothing is reported as passing that was not actually executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from codegen.generator import GeneratedFirmware
from core.exceptions import FWAgentError
from core.hardware_model import MCU
from services.build_service import BuildService
from services.toolchain import AvrToolchain

# The simulator stops on SLEEP (it is not implemented, so it faults) — that is
# how the harness signals "done" without hanging the simulator forever.
_HARNESS_TEMPLATE = """\
/* Generated test harness — not part of the firmware image. */
#include <avr/io.h>
#include <stdint.h>

#include "config.h"
#include "sensor.h"
{includes}

volatile int32_t fw_test_results[{count}];

__attribute__((noinline)) void fw_test_done(void) {{ __asm__ __volatile__("nop"); }}

int main(void)
{{
{setup}
{assignments}
    fw_test_done();
    for (;;) {{
    }}
    return 0;
}}
"""


# Sources kept out of the simulation build.
#
# main.c is replaced by the harness. watchdog.c is excluded for a different and
# more awkward reason: GDB's AVR simulator does not implement the WDR
# instruction, and faults with SIGILL on it. Since watchdog.c installs a .init3
# hook that runs before main, linking it kills every simulated run before a
# single check executes.
#
# The consequence is worth stating plainly: **the watchdog is not verified by
# simulation.** It is verified only by compiling, and would need real hardware
# to confirm.
_EXCLUDED_FROM_SIMULATION = frozenset({"main.c", "watchdog.c"})


class SimulationError(FWAgentError):
    """Raised when the simulator could not be run or its output not parsed."""


@dataclass(frozen=True)
class Check:
    """One expression to evaluate on the target.

    ``expression`` must evaluate to an integer. ``setup`` holds any C
    declarations or statements it needs, emitted once before all assignments.
    """

    name: str
    expression: str
    expected: int
    setup: str = ""


@dataclass
class CheckResult:
    name: str
    expected: int
    actual: int
    expression: str

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


@dataclass
class SimulationReport:
    """Outcome of one simulator run.

    ``status`` is ``success``, ``failed``, or ``not_run``. A report that could
    not be produced never claims passing checks.
    """

    status: str
    results: list[CheckResult] = field(default_factory=list)
    diagnostics: str = ""
    elf_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success" and all(r.passed for r in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        if self.status == "not_run":
            return f"not run: {self.diagnostics}"
        if self.status == "failed":
            return f"could not run: {self.diagnostics}"
        return f"{self.passed_count}/{len(self.results)} checks passed"


class SimulatorTestService:
    """Builds a test harness against generated firmware and runs it."""

    def __init__(self, toolchain: AvrToolchain | None = None) -> None:
        self.toolchain = toolchain or AvrToolchain()

    def run(
        self,
        firmware: GeneratedFirmware,
        mcu: MCU,
        checks: list[Check],
        work_dir: str | Path,
        f_cpu_hz: int = 16_000_000,
        timeout: int = 120,
    ) -> SimulationReport:
        if not checks:
            return SimulationReport(status="not_run", diagnostics="no checks were given")

        work_dir = Path(work_dir)
        mcu_target = BuildService.mcu_target_for(mcu)

        firmware.write_to(work_dir)
        harness_path = work_dir / "fw_test_harness.c"
        harness_path.write_text(self._render_harness(firmware, checks), encoding="utf-8")

        # Everything except main.c — the harness provides its own entry point.
        sources: list[str | Path] = [harness_path]
        sources.extend(
            work_dir / name
            for name in sorted(firmware.files)
            if name.endswith(".c") and name not in _EXCLUDED_FROM_SIMULATION
        )

        elf = work_dir / "fw_test.elf"
        build = self.toolchain.compile_to_elf(
            sources,
            elf,
            mcu=mcu_target,
            f_cpu_hz=f_cpu_hz,
            include_dirs=(work_dir,),
            extra_flags=("-g",),  # the simulator needs symbols to read results
        )
        if not build.ok:
            return SimulationReport(
                status="failed",
                diagnostics=f"harness did not build: {build.diagnostics}",
            )

        raw = self._run_simulator(elf, timeout=timeout)
        values = self._parse_results(raw, expected_count=len(checks))

        return SimulationReport(
            status="success",
            results=[
                CheckResult(
                    name=check.name,
                    expected=check.expected,
                    actual=value,
                    expression=check.expression,
                )
                for check, value in zip(checks, values)
            ],
            elf_path=elf,
            diagnostics=raw,
        )

    @staticmethod
    def _render_harness(firmware: GeneratedFirmware, checks: list[Check]) -> str:
        includes = "\n".join(
            f'#include "{name}"'
            for name in sorted(firmware.files)
            if name.endswith(".h") and name not in {"config.h", "sensor.h"}
        )
        setup = "\n".join(f"    {line}" for c in checks for line in c.setup.splitlines())
        assignments = "\n".join(
            f"    fw_test_results[{i}] = (int32_t)({check.expression});"
            for i, check in enumerate(checks)
        )
        return _HARNESS_TEMPLATE.format(
            includes=includes,
            count=len(checks),
            setup=setup,
            assignments=assignments,
        )

    def _run_simulator(self, elf: Path, timeout: int) -> str:
        command = [
            str(self.toolchain.gdb_path),
            "-batch",
            "-nx",  # ignore any developer .gdbinit
            "-ex", "set confirm off",
            "-ex", "target sim",
            "-ex", "load",
            "-ex", "break fw_test_done",
            "-ex", "run",
            "-ex", "print fw_test_results",
            str(elf),
        ]

        result = self.toolchain._run(command)  # noqa: SLF001 — same package
        if not result.ok and "fw_test_results" not in result.stdout:
            raise SimulationError(
                f"simulator run failed (exit {result.returncode}): {result.diagnostics}"
            )
        return result.stdout

    @staticmethod
    def _parse_results(raw: str, expected_count: int) -> list[int]:
        """Pull the result array out of gdb's ``$1 = {1, 2, 3}`` output."""
        match = re.search(r"\$\d+\s*=\s*\{([^}]*)\}", raw)
        if match is None:
            raise SimulationError(
                f"could not find the result array in simulator output: {raw[-500:]!r}"
            )

        values: list[int] = []
        for item in match.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            # gdb collapses runs as "0 <repeats 5 times>".
            repeat = re.match(r"(-?\d+)\s*<repeats\s+(\d+)\s+times>", item)
            if repeat:
                values.extend([int(repeat.group(1))] * int(repeat.group(2)))
                continue
            values.append(int(item))

        if len(values) != expected_count:
            raise SimulationError(
                f"expected {expected_count} results from the simulator, got {len(values)}"
            )
        return values
