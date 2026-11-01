"""Named analysis views managed inside the active engineering project."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QToolButton, QMenu

from core.analysis_templates import (KIND, STYLE_FIELDS, normalise_template,
                                     normalise_library, write_library)
from core.calculated_signals import CalculatedSignalManager, formula_references
from gui import signal_inheritance


def capture(window):
    panel = window.plot_panel
    mode = ('windows' if window.btn_windows.isChecked() else
            'multistack' if window.btn_multistack.isChecked() else
            'stacked' if window.btn_stacked.isChecked() else
            'multi_axis' if window.btn_multi_axis.isChecked() else 'normal')
    styles = [dict(key=key, **{field: getattr(item, field) for field in STYLE_FIELDS})
              for key, item in panel._items.items()]
    handoff = signal_inheritance.capture(window, {'signals': styles, 'plot_type': mode}) or {}
    presentation = dict(plot_type=mode, step_lines=panel.step_lines(),
                        show_data_points=window.btn_points.isChecked(),
                        hide_plot_lines=window.btn_hide_line.isChecked(),
                        plot_background_color=panel.background_color(), group_colors=panel.group_colors(),
                        name_show_channel=panel._name_show_channel, name_show_message=panel._name_show_message)
    if mode == 'windows':
        presentation['window_layout'] = panel.window_layout()
    else:
        presentation['y_ranges'] = {
            key: list((item.view_box or panel.plot.plotItem.vb).viewRange()[1])
            for key, item in panel._items.items()}
    needed, pending = set(), list(panel.plotted_keys())
    while pending:
        key = pending.pop()
        definition = window.calculated_signals.definition(key)
        if definition is not None and key not in needed:
            needed.add(key)
            pending.extend(formula_references(definition.formula))
    return normalise_template(dict(kind=KIND, schema_version=1, presentation=presentation,
                                  inheritance=[entry for entry in handoff.get('inheritance', []) if entry['plotted']],
                                  dependencies=handoff.get('dependencies', {}),
                                  generated_signals=[definition.to_dict() for definition in window.calculated_signals.definitions()
                                                     if definition.key in needed]))


def apply(window, template):
    if window.store is None or window._refuse_while_loading('applying an analysis template'):
        return False
    if window._calc_thread is not None:
        QMessageBox.information(window, 'Calculation in progress', 'Wait for the signal calculation to finish.')
        return False
    template = normalise_template(template)
    merged = {definition['name']: definition for definition in window.calculated_signals.to_config()}
    merged.update({definition['name']: definition for definition in template['generated_signals']})
    staged = CalculatedSignalManager()
    errors = staged.replace_definitions(merged.values())
    if errors:
        raise ValueError('\n'.join(errors))
    panel = window.plot_panel
    time_range = panel._visible_x_range()
    cursors = panel.v_line.value(), panel.v_line2.value()
    events = list(panel._events)
    show_events = panel.events_action.isChecked()
    window.playback.pause(publish=False)
    panel.clear_all()
    panel.discard_undo_history()
    panel._window_y_restore = {}
    window.calculated_signals.replace_definitions(merged.values())
    window._refresh_generated_signal_tree()
    view = template['presentation']
    window._set_step_lines(bool(view.get('step_lines', True)), announce=False)
    window.btn_points.setChecked(bool(view.get('show_data_points', True)))
    window.btn_hide_line.setChecked(bool(view.get('hide_plot_lines', False)))
    panel.set_background_color(view.get('plot_background_color', '#000000'))
    panel.set_group_colors(view.get('group_colors', {}))
    panel._name_show_channel = bool(view.get('name_show_channel', False))
    panel._name_show_message = bool(view.get('name_show_message', False))
    window._pending_plot_type = view['plot_type']
    window._apply_pending_plot_type()
    config = {**deepcopy(template), 'window_layout': view.get('window_layout', [])}
    mapping = signal_inheritance.restore(window, config)
    for old_key, bounds in view.get('y_ranges', {}).items():
        item = panel._items.get(mapping.get(old_key, old_key))
        if item is not None:
            (item.view_box or panel.plot.plotItem.vb).setYRange(*bounds, padding=0)
    panel._rebuild_seq += 1
    if time_range:
        panel._driving_vb().setXRange(*time_range, padding=0)
    panel.move_cursor1(cursors[0])
    panel.move_cursor2(cursors[1])
    blocked = panel.events_action.blockSignals(True)
    panel.events_action.setChecked(show_events)
    panel.events_action.blockSignals(blocked)
    panel.set_events(events)
    window._playback_freshness(window.playback.clock.position)
    return True


def bind_profile(window, path):
    window._project_profile_path = str(Path(path).resolve()) if path else None
    window._analysis_templates_baseline = None
    if not path:
        return
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if data.get('kind') != 'osvanta.project' or data.get('schema_version') != 1:
            raise ValueError('Not an Osvanta project')
        library = normalise_library(data['project'].get('analysis_templates', {}))
        window.project['analysis_templates'] = library
        window._analysis_templates_baseline = deepcopy(library)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        window._project_profile_path = None
        window._log(f'Project template library not linked: {exc}')


class TemplateMenu(QToolButton):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setText('分析模板')
        self.setAccessibleName('工程分析模板')
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_widget = QMenu(self)
        self.setMenu(self.menu_widget)
        self.menu_widget.aboutToShow.connect(self.populate)

    def library(self):
        return self.window.project.get('analysis_templates', {})

    def populate(self):
        menu, window = self.menu_widget, self.window
        menu.clear()
        title = menu.addAction(f'工程：{window.project.get("name") or "未命名"}')
        title.setEnabled(False)
        ready = window.store is not None and window._thread is None and window._calc_thread is None
        save = menu.addAction('保存当前分析为模板…', self.save_current)
        save.setEnabled(ready)
        menu.addSeparator()
        for name in self.library():
            action = menu.addAction(name, lambda _checked=False, name=name: self.use(name))
            action.setEnabled(ready)
        if not self.library():
            menu.addAction('本工程尚无模板').setEnabled(False)
        menu.addSeparator()
        menu.addAction('重命名模板…', self.rename).setEnabled(bool(self.library()))
        menu.addAction('删除模板…', self.delete).setEnabled(bool(self.library()))

    def persist(self, library):
        window = self.window
        profile = getattr(window, '_project_profile_path', None)
        if not profile:
            profile, _ = QFileDialog.getSaveFileName(window, '保存工程与分析模板',
                                                    (window.project.get('name') or 'project') + '.osvanta_project.json',
                                                    'Osvanta project (*.json)')
            if not profile:
                return False
            from core.project_config import save_project
            from core.bus_types import encode_key
            project = {**deepcopy(window.project), 'analysis_templates': library}
            channels = {'name': window.channel_config.name,
                        'channels': {encode_key(key): value for key, value in window.channel_config.channels.items()}}
            save_project(profile, project, channels, window.playback.to_config())
            window._project_profile_path = str(Path(profile).resolve())
        else:
            write_library(profile, library, getattr(window, '_analysis_templates_baseline', None))
        window.project['analysis_templates'] = deepcopy(library)
        window._analysis_templates_baseline = deepcopy(library)
        return True

    def save_current(self):
        name, ok = QInputDialog.getText(self.window, '保存分析模板', '模板名称')
        if not ok or not name.strip():
            return
        name = name.strip()
        library = deepcopy(self.library())
        if name in library and QMessageBox.question(self.window, '覆盖模板', f'覆盖“{name}”？') != QMessageBox.StandardButton.Yes:
            return
        try:
            library[name] = capture(self.window)
            if self.persist(library):
                self.window._log(f'Analysis template saved: {name}')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.critical(self.window, '保存模板失败', str(exc))

    def use(self, name):
        try:
            if apply(self.window, self.library()[name]):
                self.window._log(f'Analysis template applied: {name}')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.critical(self.window, '应用模板失败', str(exc))

    def choose(self, title):
        return QInputDialog.getItem(self.window, title, '模板', list(self.library()), 0, False)

    def rename(self):
        old, ok = self.choose('重命名模板')
        if not ok:
            return
        name, ok = QInputDialog.getText(self.window, '重命名模板', '新名称', text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        name = name.strip()
        if name in self.library():
            QMessageBox.information(self.window, '名称已存在', '请选择其他模板名称。')
            return
        library = {name if key == old else key: value for key, value in self.library().items()}
        self._persist_edit(library)

    def delete(self):
        name, ok = self.choose('删除模板')
        if ok and QMessageBox.question(self.window, '删除模板', f'删除“{name}”？') == QMessageBox.StandardButton.Yes:
            self._persist_edit({key: value for key, value in self.library().items() if key != name})

    def _persist_edit(self, library):
        try:
            self.persist(library)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.critical(self.window, '保存模板失败', str(exc))
