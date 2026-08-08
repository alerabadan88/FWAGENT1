"""SQLite-backed catalog of known MCUs and sensors.

Used to look up specs the input config didn't provide, and to check whether a
part is supported before the pipeline commits to a design.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.exceptions import CatalogError
from core.hardware_model import MCU, InterfaceType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcus (
    name        TEXT PRIMARY KEY,
    family      TEXT NOT NULL,
    flash_kb    REAL NOT NULL,
    ram_kb      REAL NOT NULL,
    clock_mhz   REAL NOT NULL,
    gpio_pins   INTEGER NOT NULL,
    voltage     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sensors (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    interface   TEXT NOT NULL,
    default_address TEXT
);
"""


class SensorSpec:
    """A catalog entry for a sensor part (distinct from an instance on a board)."""

    __slots__ = ("name", "type", "interface", "default_address")

    def __init__(
        self,
        name: str,
        type: str,
        interface: InterfaceType,
        default_address: str | None = None,
    ) -> None:
        self.name = name
        self.type = type
        self.interface = interface
        self.default_address = default_address

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SensorSpec):
            return NotImplemented
        return (
            self.name == other.name
            and self.type == other.type
            and self.interface == other.interface
            and self.default_address == other.default_address
        )

    def __repr__(self) -> str:
        return (
            f"SensorSpec(name={self.name!r}, type={self.type!r}, "
            f"interface={self.interface!r}, default_address={self.default_address!r})"
        )


class Catalog:
    """CRUD access to the parts catalog.

    Use as a context manager, or call :meth:`close` when done::

        with Catalog(":memory:") as catalog:
            catalog.add_mcu(mcu)
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        try:
            self._conn = sqlite3.connect(self.db_path)
        except sqlite3.Error as exc:
            raise CatalogError(f"could not open catalog database {self.db_path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- MCUs ---------------------------------------------------------------

    def add_mcu(self, mcu: MCU) -> None:
        try:
            self._conn.execute(
                "INSERT INTO mcus (name, family, flash_kb, ram_kb, clock_mhz, gpio_pins, voltage)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    mcu.name,
                    mcu.family,
                    mcu.flash_kb,
                    mcu.ram_kb,
                    mcu.clock_mhz,
                    mcu.gpio_pins,
                    mcu.voltage,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise CatalogError(f"MCU '{mcu.name}' is already in the catalog") from exc
        self._conn.commit()

    def get_mcu(self, name: str) -> MCU | None:
        row = self._conn.execute("SELECT * FROM mcus WHERE name = ?", (name,)).fetchone()
        return self._row_to_mcu(row) if row else None

    def list_mcus(self) -> list[MCU]:
        rows = self._conn.execute("SELECT * FROM mcus ORDER BY name").fetchall()
        return [self._row_to_mcu(row) for row in rows]

    def update_mcu(self, mcu: MCU) -> None:
        cursor = self._conn.execute(
            "UPDATE mcus SET family = ?, flash_kb = ?, ram_kb = ?, clock_mhz = ?,"
            " gpio_pins = ?, voltage = ? WHERE name = ?",
            (
                mcu.family,
                mcu.flash_kb,
                mcu.ram_kb,
                mcu.clock_mhz,
                mcu.gpio_pins,
                mcu.voltage,
                mcu.name,
            ),
        )
        if cursor.rowcount == 0:
            raise CatalogError(f"MCU '{mcu.name}' is not in the catalog")
        self._conn.commit()

    def remove_mcu(self, name: str) -> None:
        cursor = self._conn.execute("DELETE FROM mcus WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise CatalogError(f"MCU '{name}' is not in the catalog")
        self._conn.commit()

    @staticmethod
    def _row_to_mcu(row: sqlite3.Row) -> MCU:
        return MCU(
            name=row["name"],
            family=row["family"],
            flash_kb=row["flash_kb"],
            ram_kb=row["ram_kb"],
            clock_mhz=row["clock_mhz"],
            gpio_pins=row["gpio_pins"],
            voltage=row["voltage"],
        )

    # -- Sensors ------------------------------------------------------------

    def add_sensor(self, spec: SensorSpec) -> None:
        try:
            self._conn.execute(
                "INSERT INTO sensors (name, type, interface, default_address) VALUES (?, ?, ?, ?)",
                (spec.name, spec.type, spec.interface.value, spec.default_address),
            )
        except sqlite3.IntegrityError as exc:
            raise CatalogError(f"sensor '{spec.name}' is already in the catalog") from exc
        self._conn.commit()

    def get_sensor(self, name: str) -> SensorSpec | None:
        row = self._conn.execute("SELECT * FROM sensors WHERE name = ?", (name,)).fetchone()
        return self._row_to_sensor(row) if row else None

    def list_sensors(self) -> list[SensorSpec]:
        rows = self._conn.execute("SELECT * FROM sensors ORDER BY name").fetchall()
        return [self._row_to_sensor(row) for row in rows]

    def remove_sensor(self, name: str) -> None:
        cursor = self._conn.execute("DELETE FROM sensors WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise CatalogError(f"sensor '{name}' is not in the catalog")
        self._conn.commit()

    @staticmethod
    def _row_to_sensor(row: sqlite3.Row) -> SensorSpec:
        return SensorSpec(
            name=row["name"],
            type=row["type"],
            interface=InterfaceType(row["interface"]),
            default_address=row["default_address"],
        )
