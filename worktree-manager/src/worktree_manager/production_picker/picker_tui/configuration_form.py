"""Full-content configuration editor sharing the ordinary field mechanics."""

from typing import ClassVar

from textual.binding import Binding
from textual.containers import HorizontalScroll, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static

from .create_action_screen import CreateActionFormMixin


class ConfigurationFormScreen(CreateActionFormMixin, Screen[dict | None]):
    BINDINGS = CreateActionFormMixin.BINDINGS
    CSS = CreateActionFormMixin.CSS.replace(
        "CreateActionScreen", "ConfigurationFormScreen"
    ) + """
    ConfigurationFormScreen { align: left top; background: $background; }
    ConfigurationFormScreen > #create-frame {
        width: 100%; height: 1fr; max-height: 100%;
        border: none; padding: 1 2; overflow-y: auto;
    }
    ConfigurationFormScreen #create-summary { height: auto; }
    ConfigurationFormScreen #config-editor-heading { height: 1; padding: 0 2; }
    ConfigurationFormScreen #config-editor-sections { height: 3; width: 100%; }
    ConfigurationFormScreen #config-editor-sections Button { min-width: 12; }
    """

    def __init__(self, *args, sections=(), active_section=0, **kwargs):
        super().__init__(*args, **kwargs)
        self._sections = tuple(sections)
        self._active_section = active_section
        self.section_target = None

    def compose(self):
        yield Static(f"Configuration / {self._label}", id="config-editor-heading", markup=False)
        if self._sections:
            with HorizontalScroll(id="config-editor-sections"):
                for index, label in enumerate(self._sections):
                    yield Button(
                        label, id=f"config-editor-section-{index}",
                        variant="primary" if index == self._active_section else "default",
                    )
        yield from super().compose()

    def on_button_pressed(self, event):
        prefix = "config-editor-section-"
        if event.button.id and event.button.id.startswith(prefix):
            self.section_target = int(event.button.id[len(prefix):])
            self.dismiss(None)

    def _foot(self):
        return super()._foot() + " · Changing section cancels unsaved edits"

    @staticmethod
    def _q_label(field):
        label = CreateActionFormMixin._q_label(field)
        if field["type"] == "multichoice" and field.get("allow_other"):
            label += " One custom value per line."
        return label

    def _collect(self, *, include_hidden=False):
        return super()._collect(
            include_hidden=include_hidden, multiple_other_values=True,
        )

    def on_mount(self):
        self._restore({
            field["name"]: field["default"] for field in self._fields if "default" in field
        }, multiple_other_values=True)
        super().on_mount()


class ConfigurationResultScreen(Screen):
    CSS = """
    ConfigurationResultScreen { padding: 1 2; }
    ConfigurationResultScreen VerticalScroll { height: 1fr; }
    ConfigurationResultScreen Static { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close", show=False)]

    def __init__(self, label):
        super().__init__()
        self.label = label
        self.pending = True

    def compose(self):
        yield Static(f"Configuration / {self.label}", markup=False)
        with VerticalScroll():
            yield Static("Applying configuration...", id="config-result", markup=False)
        yield Button("Return to Configuration", id="config-result-close", disabled=True)

    def complete(self, ok, message):
        self.pending = False
        self.query_one("#config-result", Static).update(
            ("Completed\n\n" if ok else "Failed\n\n") + str(message or "")
        )
        button = self.query_one("#config-result-close", Button)
        button.disabled = False
        button.focus()

    def on_button_pressed(self, event):
        self.action_close()

    def action_close(self):
        if not self.pending:
            self.dismiss()


class ConfigurationDetailScreen(Screen):
    CSS = ConfigurationResultScreen.CSS.replace(
        "ConfigurationResultScreen", "ConfigurationDetailScreen",
    )
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, title, card):
        super().__init__()
        self.title = title
        self.card = card

    def compose(self):
        yield Static(f"Configuration / {self.title}", markup=False)
        with VerticalScroll():
            yield Static(str(self.card.get("body") or ""), markup=False)
        yield Button("Return to Configuration")

    def on_button_pressed(self, event):
        self.dismiss()

    def action_close(self):
        self.dismiss()
