"""主题系统的结构性不变量。

这些断言不是审美判断，是三类真实踩过的坑：
1. 两个主题的键不对齐 -> apply_theme 换过去后有属性停在上一个主题。
2. theme_color_map 按"色值"建查找表（retheme_widgets 只能读到控件当前的颜色值，
   读不到它来自哪个 token）。所以同一主题内两个 token 共用一个色值、在另一主题里
   却分道扬镳时，映射就有歧义——后登记的那个把前面的顶掉，换肤后控件被染成错的颜色。
3. 对比度掉到 WCAG AA 以下。
"""
from __future__ import annotations

import colorsys  # noqa: F401  (保留：调色时常用来推导候选值)
from collections import defaultdict

import pytest

from chat_exporter.ui_theme import DARK_THEME, LIGHT_THEME, _FG_KEYS


def _relative_luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# (前景 token, 背景 token, 最低对比度)
# 4.5 = 正文级 AA；3.0 = 纯图形元素（圆点、滑块）的 AA 门槛。
CONTRAST_PAIRS = [
    ("TEXT", "WINDOW", 4.5),
    ("TEXT", "SURFACE", 4.5),
    ("TEXT", "SURFACE_ALT", 4.5),
    ("TEXT", "CODE_BG", 4.5),
    ("TEXT", "SELECTION", 4.5),
    ("TEXT_SECONDARY", "SURFACE", 4.5),
    ("TEXT_MUTED", "SURFACE", 4.5),
    ("TEXT_MUTED", "SURFACE_ALT", 4.5),
    ("TEXT_ON_DARK", "SIDEBAR", 4.5),
    ("TEXT_ON_DARK_MUTED", "SIDEBAR", 4.5),
    ("SIDEBAR_TEXT_OFF", "SIDEBAR", 4.5),
    ("ON_ACCENT", "ACCENT", 4.5),
    ("ON_ACCENT", "ACCENT_HOVER", 4.5),
    ("ON_ACCENT", "ACCENT_PRESSED", 4.5),
    ("ACCENT", "SURFACE", 4.5),
    ("ACCENT_PRESSED", "ACCENT_SOFT", 4.5),
    ("BADGE_FG", "BADGE_BG", 4.5),
    ("SUCCESS", "SUCCESS_SOFT", 4.5),
    ("WARNING", "WARNING_SOFT", 4.5),
    ("DANGER", "DANGER_SOFT", 4.5),
    ("INFO", "INFO_SOFT", 4.5),
    ("AI_ACCENT", "SURFACE", 3.0),
    ("USER_ACCENT", "SURFACE", 3.0),
]

THEMES = [("light", LIGHT_THEME), ("dark", DARK_THEME)]


def test_theme_keys_match():
    """两个主题必须一一对应，否则 apply_theme 会留下上一个主题的残值。"""
    assert set(LIGHT_THEME) == set(DARK_THEME), (
        f"仅浅色有: {sorted(set(LIGHT_THEME) - set(DARK_THEME))}; "
        f"仅深色有: {sorted(set(DARK_THEME) - set(LIGHT_THEME))}"
    )


def test_every_token_is_a_hex_color():
    for name, theme in THEMES:
        for key, value in theme.items():
            assert isinstance(value, str) and value.startswith("#") and len(value) == 7, (
                f"{name} 主题的 {key} 不是 #RRGGBB：{value!r}"
            )
            int(value[1:], 16)  # 非法十六进制会在这里抛


def test_fg_keys_are_real_tokens():
    for key in _FG_KEYS:
        assert key in LIGHT_THEME, f"_FG_KEYS 里的 {key} 不是真实 token"


def test_badge_tokens_are_bucketed_correctly():
    """徽章前景走前景表、背景走背景表，串了就会被染成对方的颜色。"""
    assert "BADGE_FG" in _FG_KEYS
    assert "BADGE_BG" not in _FG_KEYS


@pytest.mark.parametrize("source,target", [("light", "dark"), ("dark", "light")])
def test_no_ambiguous_color_mapping(source, target):
    """同一主题内共用色值的 token，在另一主题里也必须去向一致。

    retheme_widgets 只看得到控件当前的色值，看不到它来自哪个 token。所以
    "浅色里 A 和 B 都是 #F0EEE6，深色里 A 要变 #1B1A18、B 要变 #242321"
    这种情况无解——后登记的赢，另一个被静默染错。
    """
    src = LIGHT_THEME if source == "light" else DARK_THEME
    dst = DARK_THEME if target == "dark" else LIGHT_THEME

    problems = []
    for bucket in ("fg", "bg"):
        keys = [k for k in src if (k in _FG_KEYS) == (bucket == "fg")]
        by_value = defaultdict(list)
        for key in keys:
            by_value[src[key].casefold()].append(key)
        for value, tokens in by_value.items():
            if len(tokens) < 2:
                continue
            targets = {dst[t] for t in tokens}
            if len(targets) > 1:
                detail = ", ".join(f"{t}->{dst[t]}" for t in tokens)
                problems.append(f"{bucket} 桶里 {value} 同时是 {tokens}，去向分歧：{detail}")

    assert not problems, f"{source}->{target} 换肤映射有歧义：\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("theme_name,theme", THEMES)
def test_contrast_meets_wcag_aa(theme_name, theme):
    failures = []
    for fg, bg, need in CONTRAST_PAIRS:
        ratio = contrast_ratio(theme[fg], theme[bg])
        if ratio < need:
            failures.append(
                f"{fg}({theme[fg]}) / {bg}({theme[bg]}) = {ratio:.2f}，低于 {need}"
            )
    assert not failures, f"{theme_name} 主题对比度不达标：\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("theme_name,theme", THEMES)
def test_scrollbar_is_visible_against_its_trough(theme_name, theme):
    """滑块和轨道同色 = 用户看不到滚动条在哪。"""
    ratio = contrast_ratio(theme["SCROLLBAR"], theme["SCROLLBAR_TROUGH"])
    assert ratio >= 1.4, f"{theme_name} 滑块/轨道对比只有 {ratio:.2f}"


def test_dual_role_tokens_land_in_both_maps():
    """既当填充又当文字的 token（状态点 bg + 状态文字 fg）两张表都要有。

    只进 bg 表的话，用 fg=Palette.SUCCESS 画的"可用"标签换肤后会停在旧色。
    """
    from chat_exporter.ui_theme import _DUAL_KEYS, theme_color_map

    for source, target in (("light", "dark"), ("dark", "light")):
        src = LIGHT_THEME if source == "light" else DARK_THEME
        dst = DARK_THEME if target == "dark" else LIGHT_THEME
        mapping = theme_color_map(source, target)
        for key in _DUAL_KEYS:
            if src[key] == dst[key]:
                continue  # 两个主题同值时本来就不需要映射
            folded = src[key].casefold()
            assert mapping["fg"].get(folded) == dst[key], f"{source}->{target}: {key} 不在前景表"
            assert mapping["bg"].get(folded) == dst[key], f"{source}->{target}: {key} 不在背景表"


@pytest.mark.parametrize("source,target", [("light", "dark"), ("dark", "light")])
def test_dual_keys_do_not_collide_with_single_role_tokens(source, target):
    """双重身份 token 登进前景表时，不能顶掉某个纯前景 token 的映射。"""
    from chat_exporter.ui_theme import _DUAL_KEYS

    src = LIGHT_THEME if source == "light" else DARK_THEME
    dst = DARK_THEME if target == "dark" else LIGHT_THEME
    fg_only = {src[k].casefold(): k for k in _FG_KEYS}
    for key in _DUAL_KEYS:
        folded = src[key].casefold()
        clash = fg_only.get(folded)
        if clash and dst[clash] != dst[key]:
            pytest.fail(f"{source}->{target}: {key} 与前景 token {clash} 同值 {folded} 且去向不同")


def test_selectcolor_is_remapped():
    """勾选框中间那个小方块也要换肤，否则深色模式下是一块白斑。"""
    from chat_exporter.ui_theme import _BG_OPTIONS

    assert "selectcolor" in _BG_OPTIONS


def test_source_accents_never_collide_with_theme_colors():
    """侧栏的来源身份色不参与换肤，所以不能和任何主题 token 同值。

    retheme_widgets 按色值查表——撞上就会被当作主题色一起染掉，
    表现为切换主题后某一个来源的色条莫名其妙变色。
    """
    from chat_exporter.gui_modern import ChatExporterGUI

    theme_values = {v.casefold() for v in LIGHT_THEME.values()}
    theme_values |= {v.casefold() for v in DARK_THEME.values()}
    collisions = {
        name: color
        for name, color in ChatExporterGUI.APP_ACCENTS.items()
        if color.casefold() in theme_values
    }
    assert not collisions, f"来源色与主题色撞值：{collisions}"


def test_apply_theme_round_trip_restores_every_value():
    """浅 -> 深 -> 浅 必须逐 token 回到原值。"""
    from chat_exporter.ui_theme import Palette, apply_theme

    apply_theme("light")
    before = {key: getattr(Palette, key) for key in LIGHT_THEME}
    apply_theme("dark")
    apply_theme("light")
    after = {key: getattr(Palette, key) for key in LIGHT_THEME}
    assert before == after


def test_unknown_theme_falls_back_to_light():
    from chat_exporter.ui_theme import Palette, apply_theme

    resolved = apply_theme("nonexistent-theme")
    assert resolved == "light"
    assert Palette.WINDOW == LIGHT_THEME["WINDOW"]
    apply_theme("light")
