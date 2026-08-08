import pytest

from core.catalog import Catalog, SensorSpec
from core.exceptions import CatalogError
from core.hardware_model import MCU, InterfaceType


@pytest.fixture
def catalog():
    with Catalog(":memory:") as cat:
        yield cat


def make_atmega() -> MCU:
    return MCU(
        name="ATmega328P",
        family="AVR",
        flash_kb=32,
        ram_kb=2,
        clock_mhz=16,
        gpio_pins=20,
        voltage=5.0,
    )


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


def test_add_and_get_mcu_roundtrips(catalog):
    mcu = make_atmega()
    catalog.add_mcu(mcu)

    fetched = catalog.get_mcu("ATmega328P")

    assert fetched == mcu


def test_get_unknown_mcu_returns_none(catalog):
    assert catalog.get_mcu("STM32F405RGT6") is None


def test_list_mcus_is_sorted_by_name(catalog):
    catalog.add_mcu(make_esp32())
    catalog.add_mcu(make_atmega())

    assert [m.name for m in catalog.list_mcus()] == ["ATmega328P", "ESP32-WROOM-32"]


def test_adding_duplicate_mcu_raises_catalog_error(catalog):
    catalog.add_mcu(make_atmega())

    with pytest.raises(CatalogError, match="already in the catalog"):
        catalog.add_mcu(make_atmega())


def test_update_mcu_changes_stored_specs(catalog):
    catalog.add_mcu(make_atmega())

    revised = make_atmega().model_copy(update={"clock_mhz": 8.0})
    catalog.update_mcu(revised)

    assert catalog.get_mcu("ATmega328P").clock_mhz == 8.0


def test_update_unknown_mcu_raises_catalog_error(catalog):
    with pytest.raises(CatalogError, match="not in the catalog"):
        catalog.update_mcu(make_esp32())


def test_remove_mcu_deletes_it(catalog):
    catalog.add_mcu(make_atmega())
    catalog.remove_mcu("ATmega328P")

    assert catalog.get_mcu("ATmega328P") is None
    assert catalog.list_mcus() == []


def test_remove_unknown_mcu_raises_catalog_error(catalog):
    with pytest.raises(CatalogError, match="not in the catalog"):
        catalog.remove_mcu("ATmega328P")


def test_add_and_get_sensor_roundtrips(catalog):
    spec = SensorSpec(
        name="MPU6050",
        type="imu_accel_gyro",
        interface=InterfaceType.I2C,
        default_address="0x68",
    )
    catalog.add_sensor(spec)

    assert catalog.get_sensor("MPU6050") == spec


def test_sensor_without_default_address_roundtrips(catalog):
    spec = SensorSpec(name="DHT22", type="temperature_humidity", interface=InterfaceType.GPIO)
    catalog.add_sensor(spec)

    fetched = catalog.get_sensor("DHT22")

    assert fetched.default_address is None
    assert fetched.interface == InterfaceType.GPIO


def test_adding_duplicate_sensor_raises_catalog_error(catalog):
    spec = SensorSpec(name="DHT22", type="temperature_humidity", interface=InterfaceType.GPIO)
    catalog.add_sensor(spec)

    with pytest.raises(CatalogError, match="already in the catalog"):
        catalog.add_sensor(spec)


def test_remove_unknown_sensor_raises_catalog_error(catalog):
    with pytest.raises(CatalogError, match="not in the catalog"):
        catalog.remove_sensor("DHT22")


def test_catalog_persists_to_a_real_file(tmp_path):
    db_path = tmp_path / "catalog.sqlite"

    with Catalog(db_path) as cat:
        cat.add_mcu(make_esp32())

    assert db_path.exists()

    with Catalog(db_path) as reopened:
        assert reopened.get_mcu("ESP32-WROOM-32") == make_esp32()
