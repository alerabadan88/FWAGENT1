import pytest

from core.exceptions import HardwareValidationError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor


def make_esp32() -> MCU:
    return MCU(
        name="ESP32-WROOM-32",
        family="ESP32",
        flash_kb=4096,
        ram_kb=520,
        clock_mhz=240,
        gpio_pins=34,
        voltage=3.3,
    )


def test_pcb_analysis_builds_graph_for_valid_config():
    mcu = make_esp32()
    sensors = [
        Sensor(name="MPU6050", type="imu_accel_gyro", interface=InterfaceType.I2C, bus="I2C1", address="0x68"),
        Sensor(name="BMP280", type="pressure_temperature", interface=InterfaceType.I2C, bus="I2C1", address="0x76"),
        Sensor(name="NEO-6M", type="gps", interface=InterfaceType.UART, bus="UART2"),
    ]

    analysis = PCBAnalysis(mcu=mcu, sensors=sensors)
    graph = analysis.to_graph()

    assert graph.number_of_nodes() == 4
    assert graph.number_of_edges() == 3
    assert graph.nodes["MCU:ESP32-WROOM-32"]["kind"] == "mcu"
    assert graph["MCU:ESP32-WROOM-32"]["SENSOR:MPU6050"]["address"] == "0x68"


def test_i2c_sensor_without_bus_or_address_is_rejected():
    with pytest.raises(HardwareValidationError, match="missing 'bus'"):
        Sensor(name="MPU6050", type="imu_accel_gyro", interface=InterfaceType.I2C)


def test_i2c_address_conflict_on_same_bus_is_rejected():
    mcu = make_esp32()
    sensors = [
        Sensor(name="MPU6050", type="imu_accel_gyro", interface=InterfaceType.I2C, bus="I2C1", address="0x68"),
        Sensor(name="OtherIMU", type="imu_accel_gyro", interface=InterfaceType.I2C, bus="I2C1", address="0x68"),
    ]

    with pytest.raises(HardwareValidationError, match="I2C address conflict"):
        PCBAnalysis(mcu=mcu, sensors=sensors)


def test_same_i2c_address_on_different_buses_is_allowed():
    mcu = make_esp32()
    sensors = [
        Sensor(name="MPU6050", type="imu_accel_gyro", interface=InterfaceType.I2C, bus="I2C1", address="0x68"),
        Sensor(name="SecondaryIMU", type="imu_accel_gyro", interface=InterfaceType.I2C, bus="I2C2", address="0x68"),
    ]

    analysis = PCBAnalysis(mcu=mcu, sensors=sensors)

    assert len(analysis.sensors) == 2
