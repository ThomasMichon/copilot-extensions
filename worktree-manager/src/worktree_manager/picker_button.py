"""Native, mouse-accessible buttons using the production Picker palette."""
from rich.style import Style
from textual.widgets import Button

from .production_picker.picker_tui.styles import C_BTN, C_BTN_SEL


def _colors(style: str) -> str:
    palette = Style.parse(style)
    return (
        f"color: {palette.color.get_truecolor().hex}; "
        f"background: {palette.bgcolor.get_truecolor().hex};"
    )


class PickerButton(Button):
    DEFAULT_CSS = f"""
    PickerButton.-style-default {{
        {_colors(C_BTN)}
        border: none; height: 1; min-height: 1; min-width: 0; width: auto;
        padding: 0 1; margin: 0 1 0 0; text-style: bold;
    }}
    PickerButton.-style-default:hover, PickerButton.-style-default:focus,
    PickerButton.-style-default.-active {{
        {_colors(C_BTN_SEL)}
        border: none; tint: transparent; background-tint: transparent;
    }}
    PickerButton:focus {{ text-style: bold underline; }}
    """
