"""Shared validation for persisted screen preferences and BLE settings."""
FONT_SIZES = (12, 16, 24, 32)


def display_packet(config):
    theme = config.get("screen_theme", "light")
    hints = config.get("screen_show_hints", True)
    size = config.get("screen_font_size", 16)
    spacing = config.get("screen_line_spacing", 7)
    if theme not in ("light", "dark"):
        raise ValueError("显示风格请选择白底黑字或黑底白字。")
    if not isinstance(hints, bool):
        raise ValueError("操作提示开关应为开启或关闭。")
    if isinstance(size, bool) or size not in FONT_SIZES or not isinstance(size, int):
        raise ValueError("字体大小支持 12、16、24、32 像素。")
    if isinstance(spacing, bool) or not isinstance(spacing, int) or not 0 <= spacing <= 16:
        raise ValueError("行间距应为 0–16 像素。")
    return {"op": "display_config", "theme": theme, "show_hints": hints,
            "font_size": size, "line_spacing": spacing}
