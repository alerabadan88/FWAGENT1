"""Tests for turning a schematic and an interview into a Zephyr board port.

Two properties carry most of the weight here:

1. A part Zephyr has no driver for is *refused*, never approximated. Binding a
   driver for a similar device is the one failure this pipeline cannot detect
   downstream -- it initialises, it reads, and it reports wrong numbers.
2. A compatible is confirmed against the binding file that declares it, not
   against Zephyr's filename convention.

What these tests do not establish is that the output builds. No Zephyr SDK is
installed, so the structural checks below are exactly that -- structural.
"""

import re

import pytest

from codegen.zephyr.binding_fetch import BindingUnavailable
from codegen.zephyr.bindings import BindingCatalog, Match
from codegen.zephyr.board_port import (
    BoardPortError,
    SocProfile,
    ZephyrBoardPort,
    _gpio_spec,
    _label,
)
from core.evidence import Claim
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from services.verifier import ContradictedClaim, VerificationService
from services.zephyr_verifier import ZephyrBindingVerifier

SOC = SocProfile(
    name="nrf52840", arch="arm", dtsi_include="nordic/nrf52840_qiaa.dtsi",
    vendor="acme", uart_label="uart0", i2c_label="i2c0", gpio_label="gpio0",
)

DHT_BINDING = '''
description: Aosong DHT sensor

compatible: "aosong,dht"

include: sensor-device.yaml

properties:
  dio-gpios:
    type: phandle-array
    required: true
  optional-thing:
    type: int
'''

INCLUDE_ONLY = '''
description: Common ADC controller properties

properties:
  "#io-channel-cells":
    type: int
'''


def mcu() -> MCU:
    return MCU(name="nrf52840", family="ARM", flash_kb=1024, ram_kb=256,
               clock_mhz=64, gpio_pins=48, voltage=3.3)


def analysis(*sensors) -> PCBAnalysis:
    return PCBAnalysis(mcu=mcu(), sensors=list(sensors))


def dht(pin="P0.13") -> Sensor:
    return Sensor(name="DHT22", type="temperature_humidity",
                  interface=InterfaceType.GPIO, pins={"pin": pin})


def button() -> Sensor:
    return Sensor(name="Button", type="user_input",
                  interface=InterfaceType.GPIO, pins={"pin": "P0.11"})


def gps() -> Sensor:
    return Sensor(name="NEO-6M", type="gnss", interface=InterfaceType.UART)


# --- Resolving a part to a driver ----------------------------------------------


def test_a_known_part_resolves_to_the_binding_zephyr_ships():
    resolution = BindingCatalog().resolve("DHT22", "temperature_humidity")

    assert resolution.match is Match.EXACT
    assert resolution.compatible == "aosong,dht"
    assert resolution.binding_path == "dts/bindings/sensor/aosong,dht.yaml"


def test_a_button_resolves_to_gpio_keys():
    assert BindingCatalog().resolve("Button", "user_input").compatible == "gpio-keys"


def test_a_receiver_with_no_binding_falls_back_to_the_generic_protocol_driver():
    """A NEO-6M has no binding; NMEA is a protocol, not a part."""
    resolution = BindingCatalog().resolve("NEO-6M", "gnss")

    assert resolution.match is Match.SUBSTITUTE
    assert resolution.compatible == "gnss-nmea-generic"


def test_the_substitute_says_what_it_gives_up():
    """Usable is not the same as equivalent, and the difference is UBX."""
    resolution = BindingCatalog().resolve("NEO-6M", "gnss")

    assert "UBX" in resolution.caveat
    assert "update rate" in resolution.caveat


def test_an_unknown_part_is_refused_rather_than_approximated():
    resolution = BindingCatalog().resolve("XYZ-9000", "temperature")

    assert resolution.match is Match.NONE
    assert not resolution.usable
    assert "Guessing a similar-looking compatible" in resolution.caveat


def test_an_unknown_part_still_offers_names_worth_checking():
    """Refusing is not the same as being unhelpful."""
    resolution = BindingCatalog().resolve("sht3x", "temperature")

    assert resolution.alternatives


def test_the_catalog_records_the_zephyr_it_came_from():
    catalog = BindingCatalog()

    assert catalog.ref == "v4.4.2"
    assert "@" in catalog.source
    assert len(catalog) > 3000


# --- Confirming a compatible against the binding itself -------------------------


def binding(text=DHT_BINDING, path="dts/bindings/sensor/aosong,dht.yaml"):
    return ZephyrBindingVerifier(
        text, source="zephyrproject-rtos/zephyr@v4.4.2", path=path
    )


def test_a_compatible_is_confirmed_from_the_binding_not_the_filename():
    claim = VerificationService([binding()]).verify(
        Claim("dht", "dt_compatible", "aosong,dht")
    )

    assert claim.authoritative
    assert "aosong,dht.yaml:4" in claim.evidence.locator


def test_a_compatible_the_binding_does_not_declare_is_a_contradiction():
    with pytest.raises(ContradictedClaim, match="declares 'aosong,dht'"):
        VerificationService([binding()]).verify(
            Claim("dht", "dt_compatible", "aosong,dht22")
        )


def test_an_include_file_declares_no_compatible_and_is_reported_as_such():
    claim = VerificationService([binding(INCLUDE_ONLY, "adc-controller.yaml")]).verify(
        Claim("adc", "dt_compatible", "adc-controller")
    )

    assert not claim.authoritative
    assert "It is an include, not a binding" in claim.evidence.note


def test_the_binding_says_which_properties_a_node_must_carry():
    assert binding().required_properties() == ["dio-gpios"]


def test_an_unpinned_zephyr_ref_is_refused_as_an_authority():
    """Bindings change between releases; 'main' is not reproducible."""
    with pytest.raises(ValueError, match="does not pin a Zephyr ref"):
        ZephyrBindingVerifier(DHT_BINDING, source="zephyr", path="x.yaml")


# --- Pins ------------------------------------------------------------------------


@pytest.mark.parametrize("given,expected", [
    ("P0.13", "<&gpio0 13 FLAGS>"),
    ("p1.5", "<&gpio1 5 FLAGS>"),
    ("PD2", "<&gpio3 2 FLAGS>"),
    ("13", "<&gpio0 13 FLAGS>"),
])
def test_pins_are_accepted_in_the_forms_people_actually_write_them(given, expected):
    assert _gpio_spec(given, SOC, "FLAGS") == expected


def test_an_unreadable_pin_is_refused_rather_than_defaulted():
    """Guessing here drives a pin nothing is connected to, and says nothing."""
    with pytest.raises(BoardPortError, match="guessing would drive the wrong pin"):
        _gpio_spec("the middle one", SOC, "FLAGS")


@pytest.mark.parametrize("name,expected", [
    ("DHT22", "dht22"), ("NEO-6M", "neo_6m"), ("HC-SR04", "hc_sr04"),
])
def test_labels_are_legal_devicetree_identifiers(name, expected):
    assert _label(name) == expected


# --- Generating the port ---------------------------------------------------------


def port_files(*sensors):
    return ZephyrBoardPort().generate(analysis(*sensors), SOC, "Acme Sensor Node v1")


def test_the_port_contains_every_file_zephyr_needs_for_an_unknown_board():
    files = port_files(dht(), button(), gps())

    assert set(files) == {
        "boards/acme/acme_sensor_node_v1/board.yml",
        "boards/acme/acme_sensor_node_v1/acme_sensor_node_v1.dts",
        "boards/acme/acme_sensor_node_v1/acme_sensor_node_v1_defconfig",
        "boards/acme/acme_sensor_node_v1/Kconfig.acme_sensor_node_v1",
        "boards/acme/acme_sensor_node_v1/board.cmake",
        "app/CMakeLists.txt",
        "app/prj.conf",
        "app/src/main.c",
        "README.md",
    }


def dts(*sensors) -> str:
    return port_files(*sensors)["boards/acme/acme_sensor_node_v1/acme_sensor_node_v1.dts"]


def test_the_devicetree_is_structurally_balanced():
    """Not a build. A generation bug that unbalances braces is worth catching."""
    text = dts(dht(), button(), gps())

    assert text.count("{") == text.count("}")
    assert text.startswith("/*")
    assert "/dts-v1/;" in text


def test_every_generated_node_declares_a_compatible():
    text = dts(dht(), button(), gps())
    nodes = re.findall(r"^\t(\w+): \w+[^\n]*\{$", text, re.MULTILINE)

    assert nodes
    assert text.count("compatible = ") >= len(nodes)


def test_the_macros_used_have_their_bindings_header_included():
    """A missing dt-bindings include is a preprocessor error at build time."""
    text = dts(dht(), button())

    assert "zephyr/dt-bindings/gpio/gpio.h" in text
    assert "zephyr/dt-bindings/input/input-event-codes.h" in text


def test_the_i2c_header_is_only_included_when_something_is_on_i2c():
    assert "dt-bindings/i2c/i2c.h" not in dts(dht())


def test_a_pin_from_the_interview_reaches_the_devicetree():
    assert "<&gpio0 13 (GPIO_PULL_UP | GPIO_ACTIVE_LOW)>" in dts(dht())


def test_an_i2c_part_becomes_an_addressed_node_on_the_bus():
    sensor = Sensor(name="BME280", type="pressure", interface=InterfaceType.I2C,
                    bus="I2C1", address="0x76")
    text = dts(sensor)

    assert "&i2c0 {" in text
    assert "bme280@76" in text
    assert "reg = <0x76>;" in text


def test_a_serial_device_is_not_put_on_the_console_uart():
    """Two devices on one peripheral interleave and both are lost."""
    text = dts(gps())

    assert "&uart1 {" in text
    assert "zephyr,console = &uart0;" in text


def test_generation_is_refused_when_a_part_has_no_driver():
    unknown = Sensor(name="XYZ-9000", type="temperature",
                     interface=InterfaceType.GPIO, pins={"pin": "P0.4"})

    with pytest.raises(BoardPortError, match="have no Zephyr driver"):
        port_files(unknown)


def test_the_refusal_names_similar_bindings_to_check():
    unknown = Sensor(name="sht3x", type="temperature",
                     interface=InterfaceType.GPIO, pins={"pin": "P0.4"})

    with pytest.raises(BoardPortError, match="Similarly named bindings"):
        port_files(unknown)


def test_a_gpio_part_with_no_pin_is_refused_and_says_who_should_answer():
    naked = Sensor(name="DHT22", type="temperature_humidity",
                   interface=InterfaceType.GPIO)

    with pytest.raises(BoardPortError, match="the interview exists to ask"):
        port_files(naked)


# --- The application ------------------------------------------------------------


def test_the_application_contains_no_register_writes():
    """The whole point: the driver was written by someone with the datasheet."""
    main = port_files(dht(), button(), gps())["app/src/main.c"]

    assert "sensor_channel_get" in main
    assert "DEVICE_DT_GET" in main
    assert not re.search(r"\b(REG|_BASE|0x4[0-9A-F]{7})\b", main)


def test_a_failed_read_is_reported_rather_than_leaving_a_stale_value():
    main = port_files(dht())["app/src/main.c"]

    assert "sample fetch failed" in main


def test_the_config_enables_only_what_the_board_actually_has():
    files = port_files(dht())

    assert "CONFIG_GNSS=y" not in files["app/prj.conf"]
    assert "CONFIG_SENSOR=y" in files["app/prj.conf"]


def test_gnss_support_is_enabled_when_there_is_a_receiver():
    assert "CONFIG_GNSS=y" in port_files(gps())["app/prj.conf"]


# --- What the README must admit ---------------------------------------------------


def readme(*sensors) -> str:
    return port_files(*sensors)["README.md"]


def test_the_readme_does_not_claim_this_port_was_built():
    """A port of this shape builds; that is not a claim about this one."""
    text = readme(dht())

    assert "**This particular port has not**" in text
    assert "building is not running" in text


def test_the_readme_separates_derived_facts_from_answered_ones():
    text = readme(dht(), button())

    assert "answered by hand" in text
    assert "fails silently when wrong" in text


def test_the_readme_carries_the_substitute_caveat_where_a_user_will_read_it():
    assert "UBX" in readme(gps())


def test_the_readme_lists_each_node_with_its_match_quality():
    text = readme(dht(), gps())

    assert "| `dht22` | DHT22 | `aosong,dht` | exact |" in text
    assert "substitute" in text


# --- Properties come from the binding, not from a table here --------------------


class StubFetcher:
    """Serves binding text without a network, so these stay offline."""

    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = texts
        self.source = "zephyrproject-rtos/zephyr@v4.4.2"

    def fetch(self, path: str) -> str:
        if path not in self._texts:
            raise BindingUnavailable(f"no stub for {path}")
        return self._texts[path]


HCSR04_BINDING = '''
description: HC-SR04 ultrasonic range finder

compatible: "hc-sr04"

properties:
  trigger-gpios:
    type: phandle-array
    required: true
  echo-gpios:
    type: phandle-array
    required: true
'''

BINDINGS = {
    "dts/bindings/sensor/aosong,dht.yaml": DHT_BINDING,
    "dts/bindings/sensor/hc-sr04.yaml": HCSR04_BINDING,
}


def checked_port() -> ZephyrBoardPort:
    return ZephyrBoardPort(fetcher=StubFetcher(BINDINGS))


def hcsr04(pins=None) -> Sensor:
    return Sensor(name="HC-SR04", type="distance", interface=InterfaceType.GPIO,
                  pins=pins or {"trigger": "P0.20", "echo": "P0.21"})


def test_the_properties_a_node_carries_are_read_from_its_binding():
    nodes = {n.label: n for n in checked_port().plan(analysis(dht(), hcsr04()))}

    assert nodes["dht22"].required_properties == ["dio-gpios"]
    assert nodes["hc_sr04"].required_properties == ["echo-gpios", "trigger-gpios"]


def test_pins_are_matched_to_properties_by_role_not_by_position():
    """A binding's trigger-gpios is filled by the pin somebody called trigger."""
    node = next(n for n in checked_port().plan(analysis(hcsr04())) if n.label == "hc_sr04")

    assert node.gpio_properties == {"trigger-gpios": "P0.20", "echo-gpios": "P0.21"}


def test_swapping_the_roles_swaps_the_pins_rather_than_keeping_the_order():
    node = next(n for n in checked_port().plan(
        analysis(hcsr04({"echo": "P0.02", "trigger": "P0.03"}))
    ) if n.label == "hc_sr04")

    assert node.gpio_properties["trigger-gpios"] == "P0.03"


def test_a_single_unnamed_pin_fills_a_single_required_property():
    """No ambiguity to resolve, so nothing is asked."""
    node = next(iter(checked_port().plan(analysis(dht()))))

    assert node.gpio_properties == {"dio-gpios": "P0.13"}


def test_a_part_missing_one_of_its_required_pins_is_refused():
    with pytest.raises(BoardPortError, match="trigger-gpios"):
        checked_port().plan(analysis(hcsr04({"echo": "P0.21"})))


def test_the_refusal_says_which_pins_it_does_have():
    with pytest.raises(BoardPortError, match="Pins recorded for it: echo"):
        checked_port().plan(analysis(hcsr04({"echo": "P0.21"})))


def test_a_binding_that_cannot_be_read_leaves_the_node_marked_unchecked():
    """Silence must be distinguishable from a clean check."""
    port = ZephyrBoardPort(fetcher=StubFetcher({}))
    node = next(iter(port.plan(analysis(dht()))))

    assert node.unchecked_reason
    assert node.required_properties == []


def test_without_a_fetcher_nothing_is_claimed_about_required_properties():
    node = next(iter(ZephyrBoardPort().plan(analysis(dht()))))

    assert node.required_properties == []


def test_both_pins_reach_the_devicetree_with_the_right_flags():
    files = checked_port().generate(analysis(hcsr04()), SOC, "Acme Node")
    text = files["boards/acme/acme_node/acme_node.dts"]

    assert "trigger-gpios = <&gpio0 20 GPIO_ACTIVE_HIGH>;" in text
    assert "echo-gpios = <&gpio0 21 GPIO_ACTIVE_HIGH>;" in text
