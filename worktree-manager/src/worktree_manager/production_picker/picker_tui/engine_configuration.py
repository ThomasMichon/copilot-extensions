"""Configuration navigation and legacy-section compatibility."""

from rich.text import Text

from .engine_helpers import C_DIM, C_TAB_ACTIVE, C_TAB_FOCUS_ON, C_TABOFF, C_WARN


class PickerConfigurationMixin:
    def _open_configuration_editor(
        self, label, fields, *, confirm, summary, submitted, submit_label=None,
    ):
        from .configuration_form import ConfigurationFormScreen
        indices = self._config_pivots()
        keys = [self._pivot_descriptor_key(index) for index in indices]
        editor = ConfigurationFormScreen(
            label, fields, confirm=confirm, submit_label=submit_label or label,
            summary=summary, confirm_values=True,
            sections=[self.htabs[index] for index in indices],
            active_section=indices.index(self.htab) if self.htab in indices else 0,
        )

        def after(values):
            if values is not None:
                submitted(values)
            elif editor.section_target is not None:
                self._configuration_preview_generation = object()
                self._restore_pivot_descriptor(keys[editor.section_target])
                self._last_config_key = self._pivot_descriptor_key()
                self.sel = ("M", 0)
                self.btn_idx = self.top = self._data_top = 0
                self.refresh()
                self.call_after_refresh(self._load_current_config_section)

        self.app.push_screen(editor, after)

    def _submit_configuration_form(self, reg, action, ctx, values):
        from .pivot_actions import format_form_template
        runtime = self._pivot_runtime(reg)
        scope = self._pivot_scope_key()
        argv = format_form_template(action.run, ctx, values)

        def work():
            return runtime.run_resolved(argv, preserve_output=True)

        def reload():
            # Reload the originating scope, never the newly selected section.
            runtime.repoll(scope)

        from worktree_manager.installation_updates import PIVOT_NAME, request_result_screen

        if reg.name == PIVOT_NAME and action.key == "update":
            self._run_command_result(action.label, work, reload, request_result_screen(action.label))
        else:
            self._run_configuration_command(action.label, work, reload)

    def _run_configuration_command(self, label, work, reload):
        from .configuration_form import ConfigurationResultScreen
        self._run_command_result(label, work, reload, ConfigurationResultScreen(label))

    def _run_command_result(self, label, work, reload, result_screen):
        self.app.push_screen(result_screen)

        def done(result):
            ok, message = result if result else (False, "Provider command failed")
            self.debug = f"{label}: {message}" if ok else f"{label} failed: {message}"
            if result_screen.is_mounted:
                result_screen.complete(ok, message)
            reload()
            self.refresh()

        self.call_after_refresh(lambda: self._run_bg(label, work, done, quiet=True))

    def _in_configuration(self):
        return self.htab in self._config_pivots()

    def _config_section_key(self):
        section = self.pivots[self.htab].get("section")
        return (section.source, section.key) if section else None

    def _configuration_tabs(self, width, focused):
        text = Text("  Sections  ", style=C_DIM)
        indices = self._config_pivots()
        active = indices.index(self.htab) if self.htab in indices else 0
        start = 0
        while start < active and (
            text.cell_len + sum(len(self.htabs[index]) + 4 for index in indices[start:active + 1])
            > width - 4
        ):
            start += 1
        if start:
            text.append("< ", style=C_DIM)
        ordered = indices[start:]
        for index in ordered:
            chip = Text(
                f" {self.htabs[index]} ",
                style=(C_TAB_FOCUS_ON if focused else C_TAB_ACTIVE)
                if index == self.htab else C_TABOFF,
            )
            if text.cell_len + chip.cell_len > width and index != self.htab:
                text.append(" ...", style=C_DIM)
                break
            text.append_text(chip)
            text.append("  ")
        text.truncate(width)
        return text

    def _rotate_config_section(self, direction):
        self._configuration_preview_generation = object()
        indices = self._config_pivots()
        self.htab = indices[(indices.index(self.htab) + direction) % len(indices)]
        self._last_config_key = self._pivot_descriptor_key()
        self.btn_idx = self.top = self._data_top = 0
        self.sel = ("M", 0)
        self.refresh()
        self.call_after_refresh(self._load_current_config_section)

    def _leave_configuration(self):
        self._configuration_preview_generation = object()
        saved = getattr(self, "_config_return", None)
        if saved is None:
            left = self._left_pivots()
            self.htab = left[0] if left else 0
            self.sel = self.default_sel()
        else:
            self.htab, self.sel, self.top, self.btn_idx = saved
            self._restore_pivot_descriptor(getattr(self, "_config_return_key", None))
            if self.htab >= len(self.pivots) or self.sel not in self.stops():
                self.htab = self._left_pivots()[0]
                self.sel = self.default_sel()
        self._config_return = None
        self.refresh()

    def _load_current_config_section(self, *, force=False):
        if self._kind() != "config_section":
            return
        section = self.pivots[self.htab]["section"]
        key = self._config_section_key()
        states = getattr(self, "_config_section_states", None)
        if states is None:
            states = self._config_section_states = {}
        if not force and key in states:
            return
        generation = object()
        states[key] = {"state": "loading", "generation": generation}
        # Legacy one-shot commands are not read-only by contract.
        if section.configure_action is None:
            states[key].update(state="ready", status=section.description, fields=None)
            self.refresh()
            return
        from . import tasks
        ctx = self._config_section_ctx()

        def work():
            ok, message = tasks.run_config_section(section, ctx)
            fields = None
            if ok:
                try:
                    fields = tasks.resolve_form_fields(
                        section.configure_action.fields, strict=True,
                    )
                except ValueError as error:
                    ok, message = False, str(error)
            return ok, message, fields

        def done(result):
            current = states.get(key)
            if current is None or current["generation"] is not generation:
                return
            ok, message, fields = result if result else (False, "Provider read failed", None)
            current.update(state="ready" if ok else "error", status=message, fields=fields)
            self.refresh()

        self._run_bg(section.label, work, done, quiet=True)

    def _activate_config_section(self):
        section = self.pivots[self.htab]["section"]
        state = getattr(self, "_config_section_states", {}).get(
            self._config_section_key(), {},
        )
        if state.get("state") == "loading":
            return
        if state.get("state") == "error":
            self._load_current_config_section(force=True)
        elif section.configure_action is None:
            from .engine_action_confirm import confirm_then
            confirm_then(self.app, section, lambda: self._run_config_section(section))
        else:
            self._open_config_action(
                section, self._config_section_ctx(), state.get("status", ""),
                state.get("fields", []),
            )


class ConfigurationSectionView:
    def __init__(self, engine):
        self._eng = engine

    def build_chrome(self, add, width, sel):
        add(self._eng.tab_bar(width, sel == ("M", 0)), stop=("M", 0))

    def build_data(self, add, width, sel):
        engine = self._eng
        section = engine.pivots[engine.htab]["section"]
        state = getattr(engine, "_config_section_states", {}).get(
            engine._config_section_key(), {"state": "loading"},
        )
        add(Text(f"  Configuration / {section.label}", style=C_TAB_ACTIVE))
        add(Text(section.description, style=C_DIM))
        if state["state"] == "loading":
            add(Text(f"  Loading {section.label}...", style=C_DIM))
            return
        for line in str(state.get("status") or "").splitlines():
            text = Text("  " + line, style=C_WARN if state["state"] == "error" else "")
            text.truncate(width)
            add(text)
        label = "Retry" if state["state"] == "error" else (
            "Edit configuration" if section.configure_action else "Run " + section.label
        )
        add(Text("  [ " + label + " ]", style=C_TAB_ACTIVE), stop=("BTN", 0))

    def build(self, add, width, sel):
        self.build_chrome(add, width, sel)
        self.build_data(add, width, sel)
