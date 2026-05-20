"""商品解析模块测试脚本。

脚本功能：
1. 自动生成 3 张本地测试图片
2. 调用 parse_product() 解析不同品类商品
3. 将结果保存到 JSON 文件，方便人工核对
"""

from __future__ import annotations

import json
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings
from ozon_selection.services.product_parser import parse_product


def main() -> None:
    """运行三个品类的解析测试，并把结果写入 JSON 文件。"""

    settings = get_settings()
    settings.product_parser_test_output_path.mkdir(parents=True, exist_ok=True)
    settings.product_parser_test_image_path.mkdir(parents=True, exist_ok=True)

    test_cases = build_test_cases(settings.product_parser_test_image_path)
    results: list[dict[str, Any]] = []

    for case in test_cases:
        print(f"Parsing test case: {case['case_id']}")
        try:
            parsed = parse_product(
                title=case["title"],
                image=case["image"],
                specs=case["specs"],
                price=case["price"],
                settings=settings,
            )
            results.append(
                {
                    "case_id": case["case_id"],
                    "input": {
                        "title": case["title"],
                        "image": case["image"],
                        "specs": case["specs"],
                        "price": case["price"],
                    },
                    "output": parsed,
                    "status": "success",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case_id": case["case_id"],
                    "input": {
                        "title": case["title"],
                        "image": case["image"],
                        "specs": case["specs"],
                        "price": case["price"],
                    },
                    "error": str(exc),
                    "status": "failed",
                }
            )

    output_path = settings.product_parser_test_output_path / f"product_parser_test_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved test result to: {output_path}")


def build_test_cases(image_dir: Path) -> list[dict[str, Any]]:
    """构造 3 个测试品类，并为每个品类生成一张本地图片。"""

    fishing_image = image_dir / "fishing_lure.png"
    clothing_image = image_dir / "hoodie.png"
    electronic_image = image_dir / "usb_c_hub.png"

    ensure_sample_png(fishing_image, theme="fishing")
    ensure_sample_png(clothing_image, theme="clothing")
    ensure_sample_png(electronic_image, theme="electronic")

    return [
        {
            "case_id": "fishing_tackle",
            "title": "Ozon 路亚铅头钩软饵套装 9cm 8g 钩号3/0 10只装",
            "image": str(fishing_image),
            "specs": [
                {"key": "类型", "value": "铅头钩软饵"},
                {"key": "长度", "value": "9 cm"},
                {"key": "克重", "value": "8 g"},
                {"key": "钩号", "value": "3/0"},
                {"key": "数量", "value": "10只"},
            ],
            "price": 399,
        },
        {
            "case_id": "apparel_hoodie",
            "title": "秋冬加绒连帽卫衣 oversize 宽松男女同款 纯棉混纺",
            "image": str(clothing_image),
            "specs": [
                {"key": "款式", "value": "连帽卫衣"},
                {"key": "面料", "value": "棉 65%, 涤纶 35%"},
                {"key": "尺码", "value": "S-XL"},
                {"key": "版型", "value": "oversize"},
                {"key": "季节", "value": "秋冬"},
            ],
            "price": 129,
        },
        {
            "case_id": "electronic_accessory",
            "title": "USB-C 扩展坞 8合1 支持 PD100W HDMI 4K RJ45 SD/TF",
            "image": str(electronic_image),
            "specs": [
                {"key": "接口", "value": "USB-C, HDMI, RJ45, USB 3.0, SD, TF"},
                {"key": "供电", "value": "PD 100W"},
                {"key": "视频输出", "value": "HDMI 4K 30Hz"},
                {"key": "材质", "value": "铝合金"},
                {"key": "颜色", "value": "深空灰"},
            ],
            "price": 199,
        },
    ]


def ensure_sample_png(path: Path, *, theme: str) -> None:
    """生成一张简单 PNG 测试图。"""

    if path.exists():
        return

    width = 160
    height = 160
    pixels = [[(248, 248, 248) for _ in range(width)] for _ in range(height)]

    if theme == "fishing":
        fill_rect(pixels, 20, 68, 130, 92, (76, 151, 255))
        fill_rect(pixels, 125, 64, 145, 96, (255, 145, 77))
        fill_rect(pixels, 40, 62, 60, 98, (42, 42, 42))
    elif theme == "clothing":
        fill_rect(pixels, 45, 40, 115, 120, (242, 130, 130))
        fill_rect(pixels, 25, 55, 45, 95, (242, 130, 130))
        fill_rect(pixels, 115, 55, 135, 95, (242, 130, 130))
        fill_rect(pixels, 60, 20, 100, 40, (220, 96, 96))
    else:
        fill_rect(pixels, 30, 55, 130, 105, (88, 88, 88))
        fill_rect(pixels, 45, 68, 58, 92, (230, 230, 230))
        fill_rect(pixels, 65, 68, 78, 92, (230, 230, 230))
        fill_rect(pixels, 85, 68, 98, 92, (230, 230, 230))
        fill_rect(pixels, 105, 68, 118, 92, (230, 230, 230))
        fill_rect(pixels, 75, 40, 115, 55, (110, 110, 110))

    path.write_bytes(encode_png(width=width, height=height, pixels=pixels))


def fill_rect(
    pixels: list[list[tuple[int, int, int]]],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: tuple[int, int, int],
) -> None:
    """给像素数组填充矩形。"""

    for y in range(max(y1, 0), min(y2, len(pixels))):
        for x in range(max(x1, 0), min(x2, len(pixels[0]))):
            pixels[y][x] = color


def encode_png(
    *,
    width: int,
    height: int,
    pixels: list[list[tuple[int, int, int]]],
) -> bytes:
    """把 RGB 像素数组编码成 PNG。"""

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    raw_rows = bytearray()
    for row in pixels:
        raw_rows.append(0)
        for r, g, b in row:
            raw_rows.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=9)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", idat),
            chunk(b"IEND", b""),
        ]
    )


if __name__ == "__main__":
    main()
