"""Command-line entry point for fw-automation-agent.

    fw-agent generate examples/arduino-uno/config.json -o build/
    fw-agent build    examples/arduino-uno/config.json
    fw-agent verify   examples/arduino-uno/config.json
    fw-agent inspect  examples/arduino-uno/config.json

Exit status is 0 only when the requested step actually succeeded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codegen.generator import generate_firmware
from core.eda_parser import parse_config_file
from core.exceptions import FWAgentError
from services.build_service import BuildService
from services.test_service import Check, SimulatorTestService
from services.toolchain import AvrToolchain

# Checks that exercise the generated drivers' arithmetic on the target itself.
# Keyed by driver kind so a board only runs the ones that apply.
_DRIVER_CHECKS: dict[str, list[Check]] = {
    "ultrasonic": [
        Check("ultrasonic: 1000 ticks is 686 mm", "{sym}_ticks_to_mm(1000)", 686),
        Check("ultrasonic: no 16-bit overflow at range", "{sym}_ticks_to_mm(9500)", 6517),
        Check("ultrasonic: zero ticks is zero mm", "{sym}_ticks_to_mm(0)", 0),
    ],
    "single_wire": [
        Check(
            "dht22: valid frame accepted",
            "{sym}_decode({sym}_good, &{sym}_t, &{sym}_rh)",
            0,
            setup=(
                "uint8_t {sym}_good[5] = {{0x02,0x92,0x01,0x0D,0xA2}};"
                " int16_t {sym}_t = 0; uint16_t {sym}_rh = 0;"
            ),
        ),
        Check("dht22: humidity decodes to 65.8 %", "{sym}_rh", 658),
        Check("dht22: temperature decodes to 26.9 C", "{sym}_t", 269),
        Check(
            "dht22: corrupt checksum rejected",
            "{sym}_decode({sym}_bad, 0, 0)",
            2,
            setup="uint8_t {sym}_bad[5] = {{0x02,0x92,0x01,0x0D,0x00}};",
        ),
    ],
}


def _checks_for(firmware_sensors) -> list[Check]:
    checks: list[Check] = []
    for sensor in firmware_sensors:
        for template in _DRIVER_CHECKS.get(sensor.driver_kind, []):
            sym = sensor.symbol.lower()
            checks.append(
                Check(
                    # ASCII only: the Windows console default codepage mangles
                    # anything else.
                    name=f"{sensor.name}: {template.name}",
                    expression=template.expression.format(sym=sym),
                    expected=template.expected,
                    setup=template.setup.format(sym=sym),
                )
            )
    return checks


def _load(config: Path):
    analysis = parse_config_file(config)
    firmware = generate_firmware(analysis)
    return analysis, firmware


def cmd_inspect(args: argparse.Namespace) -> int:
    analysis = parse_config_file(args.config)
    mcu = analysis.mcu

    print(f"MCU     {mcu.name} ({mcu.family})")
    print(f"        {mcu.flash_kb:.0f} KB flash, {mcu.ram_kb:.0f} KB RAM, "
          f"{mcu.clock_mhz:.0f} MHz, {mcu.voltage} V")
    print(f"Sensors {len(analysis.sensors)}")
    for sensor in analysis.sensors:
        where = sensor.address and f"{sensor.bus}@{sensor.address}" or sensor.bus or (
            ", ".join(f"{k}={v}" for k, v in (sensor.pins or {}).items())
        )
        flag = "" if sensor.required else "  (optional)"
        print(f"        - {sensor.name:<10} {sensor.interface.value:<7} {where}{flag}")

    graph = analysis.to_graph()
    print(f"Graph   {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    _, firmware = _load(args.config)
    written = firmware.write_to(args.output)

    print(f"Wrote {len(written)} files to {Path(args.output).resolve()}")
    for path in sorted(written, key=lambda p: p.name):
        print(f"  {path.name:<20} {path.stat().st_size:>6} B")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    analysis, firmware = _load(args.config)
    result = BuildService().build(
        firmware, analysis.mcu, args.output, f_cpu_hz=args.f_cpu
    )

    if not result.ok:
        print(f"Build FAILED for {analysis.mcu.name}", file=sys.stderr)
        print(result.diagnostics, file=sys.stderr)
        return 1

    memory = result.memory
    print(f"Build OK   {result.elf_path}")
    print(f"  flash    {memory.flash_used_bytes:>6} / {memory.flash_capacity_bytes} B "
          f"({memory.flash_percent} %)")
    print(f"  RAM      {memory.ram_used_bytes:>6} / {memory.ram_capacity_bytes} B "
          f"({memory.ram_percent} %)")
    if not memory.fits:
        print("  DOES NOT FIT on this part", file=sys.stderr)
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    analysis, firmware = _load(args.config)

    build = BuildService().build(firmware, analysis.mcu, args.output, f_cpu_hz=args.f_cpu)
    if not build.ok:
        print("Build FAILED — not running checks", file=sys.stderr)
        print(build.diagnostics, file=sys.stderr)
        return 1
    print(f"Build OK   flash {build.memory.flash_percent} %, RAM {build.memory.ram_percent} %")

    if not AvrToolchain.simulator_available():
        print("Simulator (avr-gdb) not available — checks NOT run.", file=sys.stderr)
        return 1

    from codegen.generator import _resolve_sensor  # noqa: PLC0415 — internal detail

    checks = _checks_for([_resolve_sensor(s) for s in analysis.sensors])
    if not checks:
        print("No on-target checks apply to this board.")
        return 0

    report = SimulatorTestService().run(
        firmware, analysis.mcu, checks, args.output, f_cpu_hz=args.f_cpu
    )
    if report.status != "success":
        print(f"Checks could not run: {report.diagnostics}", file=sys.stderr)
        return 1

    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else f"  (expected {result.expected}, got {result.actual})"
        print(f"  {mark}  {result.name}{detail}")

    print(f"\n{report.summary()} on a simulated {analysis.mcu.name}")
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fw-agent",
        description="Generate, build, and verify embedded firmware from a hardware config.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser, *, with_output: bool = True) -> None:
        sub.add_argument("config", type=Path, help="hardware config JSON")
        if with_output:
            sub.add_argument(
                "-o", "--output", type=Path, default=Path("build"),
                help="output directory (default: build/)",
            )
            sub.add_argument(
                "--f-cpu", type=int, default=16_000_000,
                help="CPU frequency in Hz (default: 16000000)",
            )

    inspect = subparsers.add_parser("inspect", help="parse the config and describe the board")
    add_common(inspect, with_output=False)
    inspect.set_defaults(func=cmd_inspect)

    generate = subparsers.add_parser("generate", help="write firmware sources")
    add_common(generate)
    generate.set_defaults(func=cmd_generate)

    build = subparsers.add_parser("build", help="generate, compile, and report real memory use")
    add_common(build)
    build.set_defaults(func=cmd_build)

    verify = subparsers.add_parser(
        "verify", help="build, then run driver checks on a simulated MCU"
    )
    add_common(verify)
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FWAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
