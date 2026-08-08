"""Build generated firmware, optionally against fetched driver libraries.

Memory figures come from ``avr-size`` reading the real ELF. Nothing here
estimates, and a build that did not run reports ``status="not_run"`` rather
than a plausible-looking number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codegen.generator import GeneratedFirmware
from core.hardware_model import MCU
from services.driver_fetcher import InstalledDriver
from services.toolchain import AvrToolchain, CompileResult

# Arduino Uno maps the AVR part to this avr-gcc -mmcu value.
MCU_TARGETS = {
    "ATMEGA328P": "atmega328p",
    "ATMEGA168": "atmega168",
    "ATMEGA2560": "atmega2560",
}


@dataclass
class MemoryReport:
    """Real flash/RAM usage, measured from the built ELF."""

    text_bytes: int
    data_bytes: int
    bss_bytes: int
    flash_capacity_bytes: int
    ram_capacity_bytes: int

    @property
    def flash_used_bytes(self) -> int:
        # Flash holds code plus the initializers for initialized globals.
        return self.text_bytes + self.data_bytes

    @property
    def ram_used_bytes(self) -> int:
        # Static RAM: initialized globals plus zero-initialized ones.
        return self.data_bytes + self.bss_bytes

    @property
    def flash_percent(self) -> float:
        return round(100.0 * self.flash_used_bytes / self.flash_capacity_bytes, 2)

    @property
    def ram_percent(self) -> float:
        return round(100.0 * self.ram_used_bytes / self.ram_capacity_bytes, 2)

    @property
    def fits(self) -> bool:
        return (
            self.flash_used_bytes <= self.flash_capacity_bytes
            and self.ram_used_bytes <= self.ram_capacity_bytes
        )


@dataclass
class BuildResult:
    """Outcome of one build attempt.

    ``status`` is one of ``success``, ``failed``, or ``not_run``. A build that
    could not be attempted never reports success.
    """

    status: str
    mcu_target: str
    build_dir: Path
    elf_path: Path | None = None
    memory: MemoryReport | None = None
    diagnostics: str = ""
    drivers: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"


class BuildService:
    """Compiles generated firmware with the real AVR toolchain."""

    def __init__(self, toolchain: AvrToolchain | None = None) -> None:
        self.toolchain = toolchain or AvrToolchain()

    @staticmethod
    def mcu_target_for(mcu: MCU) -> str:
        target = MCU_TARGETS.get(mcu.name.upper())
        if target is None:
            raise ValueError(
                f"no avr-gcc -mmcu target is known for '{mcu.name}' "
                f"(known: {sorted(MCU_TARGETS)})"
            )
        return target

    def build(
        self,
        firmware: GeneratedFirmware,
        mcu: MCU,
        build_dir: str | Path,
        f_cpu_hz: int = 16_000_000,
        drivers: list[InstalledDriver] | None = None,
    ) -> BuildResult:
        """Write, compile, and link firmware; measure the result."""
        drivers = drivers or []
        build_dir = Path(build_dir)
        mcu_target = self.mcu_target_for(mcu)
        driver_names = [f"{d.spec.library_name}@{d.spec.version}" for d in drivers]

        firmware.write_to(build_dir)

        # Every generated .c is compiled; main.c leads so link order is stable.
        generated_sources = sorted(
            name for name in firmware.files if name.endswith(".c") and name != "main.c"
        )
        sources: list[str | Path] = [build_dir / "main.c"]
        sources.extend(build_dir / name for name in generated_sources)
        include_dirs: list[str | Path] = [build_dir]
        for driver in drivers:
            sources.extend(driver.source_files)
            include_dirs.extend(driver.include_dirs or [driver.root])

        syntax = self.toolchain.check_syntax(
            build_dir / "main.c",
            mcu=mcu_target,
            f_cpu_hz=f_cpu_hz,
            include_dirs=tuple(include_dirs),
        )
        if not syntax.ok:
            return BuildResult(
                status="failed",
                mcu_target=mcu_target,
                build_dir=build_dir,
                diagnostics=syntax.diagnostics,
                drivers=driver_names,
                commands=[syntax.command],
            )

        elf_path = build_dir / "firmware.elf"
        link: CompileResult = self.toolchain.compile_to_elf(
            sources,
            elf_path,
            mcu=mcu_target,
            f_cpu_hz=f_cpu_hz,
            include_dirs=tuple(include_dirs),
        )
        if not link.ok:
            return BuildResult(
                status="failed",
                mcu_target=mcu_target,
                build_dir=build_dir,
                diagnostics=link.diagnostics,
                drivers=driver_names,
                commands=[syntax.command, link.command],
            )

        sizes = self.toolchain.section_sizes(elf_path)
        memory = MemoryReport(
            text_bytes=sizes["text"],
            data_bytes=sizes["data"],
            bss_bytes=sizes["bss"],
            flash_capacity_bytes=int(mcu.flash_kb * 1024),
            ram_capacity_bytes=int(mcu.ram_kb * 1024),
        )

        return BuildResult(
            status="success",
            mcu_target=mcu_target,
            build_dir=build_dir,
            elf_path=elf_path,
            memory=memory,
            diagnostics=link.diagnostics,
            drivers=driver_names,
            commands=[syntax.command, link.command],
        )
