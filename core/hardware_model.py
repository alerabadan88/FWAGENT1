from __future__ import annotations

from collections import defaultdict
from enum import Enum

import networkx as nx
from pydantic import BaseModel, Field, model_validator

from core.exceptions import HardwareValidationError


class InterfaceType(str, Enum):
    I2C = "I2C"
    SPI = "SPI"
    UART = "UART"
    GPIO = "GPIO"
    ADC = "ADC"
    ONE_WIRE = "1-Wire"


class MCU(BaseModel):
    name: str
    family: str
    flash_kb: float = Field(gt=0)
    ram_kb: float = Field(gt=0)
    clock_mhz: float = Field(gt=0)
    gpio_pins: int = Field(gt=0)
    voltage: float = Field(gt=0)


class Sensor(BaseModel):
    name: str
    type: str
    interface: InterfaceType
    bus: str | None = None
    address: str | None = None
    pins: dict[str, str] | None = None
    required: bool = True

    @model_validator(mode="after")
    def _check_i2c_addressing(self) -> Sensor:
        if self.interface == InterfaceType.I2C and (self.bus is None or self.address is None):
            raise HardwareValidationError(
                f"Sensor '{self.name}' uses I2C but is missing 'bus' and/or 'address'"
            )
        return self


class PCBAnalysis(BaseModel):
    mcu: MCU
    sensors: list[Sensor] = Field(default_factory=list)
    board: str | None = Field(
        default=None,
        description=(
            "Board the part sits on, when there is one. Only needed to resolve "
            "silkscreen labels like 'D2', which mean different chip pins on "
            "different boards; MCU-native pins such as PD2 need no board."
        ),
    )

    @model_validator(mode="after")
    def _check_i2c_address_conflicts(self) -> PCBAnalysis:
        by_bus_address: dict[tuple[str, str], list[str]] = defaultdict(list)
        for sensor in self.sensors:
            if sensor.interface == InterfaceType.I2C:
                by_bus_address[(sensor.bus, sensor.address)].append(sensor.name)

        conflicts = {key: names for key, names in by_bus_address.items() if len(names) > 1}
        if conflicts:
            details = "; ".join(
                f"{names} share {bus}@{address}" for (bus, address), names in conflicts.items()
            )
            raise HardwareValidationError(f"I2C address conflict(s): {details}")
        return self

    def to_graph(self) -> nx.Graph:
        graph = nx.Graph()
        mcu_node = f"MCU:{self.mcu.name}"
        graph.add_node(mcu_node, kind="mcu", data=self.mcu)

        for sensor in self.sensors:
            sensor_node = f"SENSOR:{sensor.name}"
            graph.add_node(sensor_node, kind="sensor", data=sensor)
            graph.add_edge(
                mcu_node,
                sensor_node,
                interface=sensor.interface.value,
                bus=sensor.bus,
                address=sensor.address,
            )

        return graph
