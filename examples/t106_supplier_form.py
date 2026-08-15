"""Export the outstanding items as a bilingual form the supplier can fill in.

    python -m examples.t106_supplier_form <spec.xlsx> [output directory]

The list is generated from the same call the emitter makes, so the form cannot
drift from what the tool actually requires. Answer every blocking row and
generation proceeds; leave one and it refuses, by design.

Translations live here rather than in `knowledge/questions.py` because the
knowledge base is not a localisation layer -- it enumerates what is unknown,
and the shape of that list is the same in any language. Only the presentation
is bilingual.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from examples.t106_from_spec import devices_from, read_sheet
from knowledge.base import KnowledgeBase
from knowledge.board import BoardFacts
from knowledge.questions import advisory, blocking, board_questions, family_questions

#: Device identifier -> how the source document names it, with the row.
DEVICE_ZH = {
    "home key": ("Home 键 / 开机键", "硬件定义 r17"),
    "led red": ("红灯（电源 / 电池）", "软件定义 r10"),
    "led blue": ("蓝灯（GPS 定位）", "软件定义 r10"),
    "led green": ("绿灯（网络 / 服务器）", "软件定义 r10"),
    "da267": ("DA267 加速度计（G-Sensor）", "硬件定义 r69"),
    "AG3335A": ("AG3335A GNSS 接收机", "硬件定义 r72"),
}

#: Question kind -> Chinese phrasing. `{name}` is the device.
QUESTION_ZH = {
    "pins.in": "{name} 接到哪个引脚？",
    "pins.out": "{name} 接到哪个引脚？",
    "active_level": "{name} 是高电平有效还是低电平有效？",
    "pull": "{name} 需要内部上拉、内部下拉，还是都不需要（板上已有外部电阻）？",
    "bus.i2c": "{name} 挂在哪一路 I²C 控制器上？",
    "bus.uart": "{name} 接在哪一路 UART 上？",
    "address": (
        "{name} 在本板上的 7 位 I²C 地址是多少？"
        "请查看地址选择引脚（ADDR / SDO）接高还是接低 —— 仅凭型号无法确定地址。"
    ),
    "baud": "{name} 使用的波特率是多少？",
    "intent": "用一两句话说明本固件要实现什么？",
    "loop_ms": "主循环多久执行一次（毫秒）？",
    "sdk.local_path": (
        "UNISOC UWS6121E 的 SDK 是否已在本机解压？如有，请给出根目录路径。"
        "（仅在本地解析头文件，不上传任何内容）"
    ),
    "pin_syntax": "UWS6121E 的 SDK 如何命名引脚？请给出一个示例。",
    "os_model": "UWS6121E 的固件运行在 RTOS（有任务调度）下，还是裸机单循环？",
}

#: Failure mode -> Chinese. These are why a row is blocking, and they are the
#: part most worth translating accurately: they are the argument for answering.
FAILURE_ZH = {
    "a pin that is never actually touched, silently":
        "固件实际操作的是另一个引脚。若该引脚悬空，会读到稳定但无意义的数值并当作数据上报，且不报任何错误。",
    "every state inverted, with no error anywhere":
        "该引脚的极性整体反转：输出时该亮却灭、该灭却亮；输入时按下读为松开。"
        "固件不报任何错误，看起来完全正常。",
    "phantom presses that only appear in the field":
        "输入悬空会读到噪声，按键随机自触发，且这种现象通常只在现场出现。",
    "a device that never answers":
        "数据发往该器件并不在的总线，所有读操作超时。",
    "readings from the wrong part, in range and wrong":
        "总线上另一个器件应答，返回的数值落在合理范围内，但完全是错的。",
    "a silent port, or a console fighting a sensor":
        "该端口收不到数据；更糟的情况是占用了调试串口，日志与传感器数据互相干扰。",
    "unparseable bytes -- loud, and quick to spot":
        "收到乱码。现象明显，容易及时发现。",
    "a battery life that misses target with no visible symptom":
        "电流按同样倍数偏离，续航达不到目标，且固件不会报告任何异常。",
    "firmware nobody can review against an intent":
        "没有人能对照需求评审这份固件。",
    "a porting layer that cannot be completed":
        "移植层无法填写，只能以桩函数交付。",
    "generated code that is correct but under-commented":
        "生成的代码正确，但注释不足。",
}

ASKED_OF_ZH = {
    "the person who wired the board": "硬件工程师（画板 / 接线的人）",
    "the firmware engineer": "软件工程师",
    "whoever set the power budget": "定义功耗预算的人",
    "whoever specified the product": "产品定义",
    "whoever chose the part": "选型负责人",
}


def kind_of(field: str, board: BoardFacts) -> str:
    """Map a question's field onto a translation key."""
    if not field.startswith("devices["):
        return field
    index = int(field[len("devices["):field.index("]")])
    tail = field.split("].", 1)[1]
    if tail == "bus":
        return f"bus.{board.devices[index].interface}"
    return tail


def device_of(field: str, board: BoardFacts):
    if not field.startswith("devices["):
        return None
    return board.devices[int(field[len("devices["):field.index("]")])]


def bilingual(item, board: BoardFacts) -> dict:
    device = device_of(item.field, board)
    name_en = device.name if device else ""
    name_zh, row = DEVICE_ZH.get(name_en, (name_en, ""))

    template = QUESTION_ZH.get(kind_of(item.field, board))
    zh = template.format(name=name_zh) if template else item.question
    return {
        "field": item.field,
        "zh": zh,
        "en": item.question,
        "failure_zh": FAILURE_ZH.get(item.failure, item.failure),
        "failure_en": item.failure,
        "owner": ASKED_OF_ZH.get(item.asked_of, item.asked_of),
        "owner_en": item.asked_of,
        "default": item.default or "",
        "source": row,
    }


def build(spec: Path):
    rows = read_sheet(spec, "硬件定义")
    mcu = next((r.spec for r in rows if "Chipset" in r.item), "")
    name = next((r.spec for r in rows if "PCBA name" in r.item), spec.stem)
    devices, sourced, skipped = devices_from(rows)
    board = BoardFacts(board_name=name, mcu=mcu, devices=devices)
    family = KnowledgeBase().resolve(mcu)

    questions = board_questions(board, family)
    return {
        "spec": spec.name,
        "board": board,
        "family": family,
        "sourced": sourced,
        "skipped": skipped,
        "blocking": [bilingual(q, board) for q in blocking(questions)],
        "advisory": [bilingual(q, board) for q in advisory(questions)],
        "family_items": [bilingual(q, board) for q in family_questions(family)] if family else [],
    }


def markdown(data) -> str:
    board = data["board"]
    n_block = len(data["blocking"])
    lines = [
        "# T106 固件开发 —— 待确认事项",
        "# T106 Firmware — Outstanding Items",
        "",
        f"来源 / Source: `{data['spec']}`  ",
        f"生成日期 / Generated: {date.today().isoformat()}  ",
        f"目标芯片 / Target: `{board.mcu}`　主板 / Board: `{board.board_name}`",
        "",
        "本清单由工具直接生成，不是人工整理的。  ",
        "This list is generated by the tool itself, not compiled by hand.",
        "",
        f"**{n_block} 项为阻塞项**：答复齐全即可生成固件；缺任何一项，生成会被拒绝。  ",
        f"**{n_block} items are blocking**: answer them all and firmware is generated; "
        "leave one and generation is refused.",
        "",
        "> 为什么阻塞：这些值如果猜错，固件**照样编译、照样运行**，只是行为是错的。  ",
        "> 产线测试查不出来。因此不设默认值，必须由看过原理图的人回答。  ",
        "> *Why blocking: if guessed wrong, the firmware still builds and still runs — "
        "it is merely wrong. The production line cannot catch this. "
        "So there is no default; a person who has seen the schematic must answer.*",
        "",
        "填写方式 / How to fill in: 请在「答复」列作答。无法作答的，请写明**由谁**在**何时**给出。  ",
        "Answer in the *Answer* column. If not yet available, state **who** will "
        "provide it and **by when**.",
        "",
        "---",
        "",
        "## 0. 工具从规格书读到的器件 / Devices read from the specification",
        "",
        "请先确认这份解读是否正确。/ Please confirm this reading is correct first.",
        "",
        "| 规格书行 Row | 器件 Device |",
        "|---|---|",
    ]
    for number, what in data["sourced"]:
        lines.append(f"| r{number} | {what} |")
    lines += [
        "",
        f"规格书中另有 {len(data['skipped'])} 行声明了内容，但本工具当前不建模"
        "（摄像头、音频、SIM、充电等需要额外的 HAL 支持）。  ",
        f"A further {len(data['skipped'])} rows declare something the tool does not "
        "model today (camera, audio, SIM and charging need HAL surface that does "
        "not exist yet).",
        "",
        "---",
        "",
        "## A. 阻塞项 —— 必须回答 / Blocking — must be answered",
        "",
        "| # | 中文 | English | 若答错会怎样 / If wrong | 答复 Answer |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(data["blocking"], 1):
        lines.append(
            f"| A{index} | {item['zh']} | {item['en']} | "
            f"{item['failure_zh']}<br>*{item['failure_en']}* | |"
        )

    owners = dict.fromkeys(
        f"{item['owner']} · {item['owner_en']}" for item in data["blocking"]
    )
    lines += [
        "",
        "负责人 / Owner: " + ("；".join(owners) if owners else "-"),
        "",
        "---",
        "",
        "## B. 建议确认 —— 有默认值 / Advisory — a default exists",
        "",
        "这些如果答错，现象明显（乱码、续航不达标），因此有默认值。  ",
        "A wrong answer here fails loudly, so a default is acceptable.",
        "",
        "| # | 中文 | English | 默认值 Default | 答复 Answer |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(data["advisory"], 1):
        lines.append(
            f"| B{index} | {item['zh']} | {item['en']} | "
            f"`{item['default'] or '—'}` | |"
        )

    lines += [
        "",
        "---",
        "",
        "## C. SDK 与工具链 / SDK and toolchain",
        "",
        "这些决定移植层能否填写真实的 SDK 调用，还是只能交付桩函数。  ",
        "These decide whether the porting layer can carry real SDK calls or only stubs.",
        "",
        "| # | 中文 | English | 答复 Answer |",
        "|---|---|---|---|",
    ]
    for index, item in enumerate(data["family_items"], 1):
        lines.append(f"| C{index} | {item['zh']} | {item['en']} | |")

    lines += [
        "",
        "> SDK 受 NDA 限制也没关系：解析在本机进行，头文件不会上传，"
        "只有函数名与签名会记入本地知识库。  ",
        "> *An NDA-gated SDK is fine: parsing happens locally, headers are never "
        "uploaded, and only names and signatures enter the local knowledge base.*",
        "",
        "---",
        "",
        "## 回答之后会得到什么 / What answering produces",
        "",
        "- `app/` —— 完整的应用逻辑：三色灯状态机、按键去抖、NMEA 解析、调度。"
        "与厂商 SDK 无关，已用 `-Wall -Wextra -Werror` 交叉编译通过。  ",
        "  `app/` — complete application logic, vendor-independent, and it compiles "
        "clean under `-Wall -Wextra -Werror`.",
        "- `port/` —— 14 个函数的移植层。有 SDK 就填真实调用，没有就是带问题的桩函数。  ",
        "  `port/` — a fourteen-function porting layer, filled in when the SDK is "
        "present and stubbed with the open question when it is not.",
        "- `PROVENANCE.md` —— 每个数值的来源，人工回答的与从工件推导的分开列出。  ",
        "  `PROVENANCE.md` — where every value came from, human answers kept "
        "separate from artifact-derived facts.",
        "",
        "**本工具不烧录，也不需要烧录工具存在。** 固件由工程师自行烧录。  ",
        "**The tool does not flash and does not require a flashing tool to exist.** "
        "An engineer flashes it.",
        "",
    ]
    return "\n".join(lines)


def write_csv(data, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "编号 No.", "类别 Category", "中文", "English",
            "若答错 If wrong (中文)", "If wrong (EN)",
            "默认值 Default", "答复 Answer", "负责人 Owner", "承诺日期 Due",
        ])
        for prefix, key, category in (
            ("A", "blocking", "阻塞项 Blocking"),
            ("B", "advisory", "建议 Advisory"),
            ("C", "family_items", "SDK 与工具链 SDK/toolchain"),
        ):
            for index, item in enumerate(data[key], 1):
                writer.writerow([
                    f"{prefix}{index}", category, item["zh"], item["en"],
                    item["failure_zh"], item["failure_en"],
                    item["default"], "", item["owner"], "",
                ])


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    spec = Path(argv[1])
    if not spec.is_file():
        print(f"not found: {spec}")
        return 2
    out = Path(argv[2]) if len(argv) > 2 else spec.parent

    data = build(spec)
    md = out / "T106_FW_outstanding_items_ZH-EN.md"
    csv_path = out / "T106_FW_outstanding_items_ZH-EN.csv"
    md.write_text(markdown(data), encoding="utf-8")
    write_csv(data, csv_path)

    print(f"{len(data['blocking'])} blocking, {len(data['advisory'])} advisory, "
          f"{len(data['family_items'])} SDK")
    print(md)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
