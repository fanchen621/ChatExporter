"""生成 ChatExporter 的应用图标（app.ico + window.png）。

设计：品牌紫渐变的圆角方块 + 白色对话气泡，气泡里一支向上的导出箭头。
运行一次即可重新生成资产；打包与窗口图标都引用生成结果。
依赖 pillow（仅构建期，运行期不需要）。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

BASE = 1024
ACCENT_TOP = (217, 119, 87)     # #D97757 Claude 珊瑚
ACCENT_BOTTOM = (156, 74, 47)   # #9C4A2F 深陶土
WHITE = (250, 249, 245, 255)    # #FAF9F5 象牙，纯白在暖底上会发蓝


def _rounded_gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(ACCENT_TOP, ACCENT_BOTTOM)) + (255,)
        gradient.putpixel((0, y), color)
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    radius = int(size * 0.22)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.paste(gradient, (0, 0), mask)
    return img


def _draw_mark(img: Image.Image) -> None:
    size = img.width
    draw = ImageDraw.Draw(img)

    # 对话气泡：圆角矩形 + 左下小尾巴
    bx0, by0 = int(size * 0.20), int(size * 0.24)
    bx1, by1 = int(size * 0.80), int(size * 0.66)
    draw.rounded_rectangle((bx0, by0, bx1, by1), radius=int(size * 0.09), fill=WHITE)
    draw.polygon(
        [
            (int(size * 0.30), by1 - 2),
            (int(size * 0.30), int(size * 0.80)),
            (int(size * 0.44), by1 - 2),
        ],
        fill=WHITE,
    )

    # 导出箭头（气泡内，品牌紫）：箭杆 + 箭头，往上示意“导出去”
    cx = (bx0 + bx1) // 2
    shaft_w = int(size * 0.055)
    top = int(size * 0.315)
    bottom = int(size * 0.585)
    draw.rectangle((cx - shaft_w // 2, top + int(size * 0.06), cx + shaft_w // 2, bottom), fill=ACCENT_BOTTOM)
    head = int(size * 0.115)
    draw.polygon(
        [(cx - head, top + int(size * 0.10)), (cx + head, top + int(size * 0.10)), (cx, top - int(size * 0.02))],
        fill=ACCENT_BOTTOM,
    )


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    icon = _rounded_gradient(BASE)
    _draw_mark(icon)

    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "window.png"))
    icon.save(
        os.path.join(here, "app.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("written:", os.path.join(here, "app.ico"), "and window.png")


if __name__ == "__main__":
    main()
