# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The analysis assistant: a docked panel that analyses what is currently
on the plot, shows findings with evidence, and navigates the plot back to
each piece of evidence.

Local analysis reads decoded signals. The optional Agent uses a detached,
user-approved signal and time scope.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.assistant_analysis import (
    AnalysisContext, AnalysisResult, Evidence, Finding, MAX_ANALYSIS_KEYS,
    measurement_source_id,
)
from core.assistant_tools import AnalysisTools
from core.codex_bridge import BridgeError, Cancelled
from core.desktop_chats import DesktopChats, DesktopProject, MAX_QUESTION, chat_url
from core.shared_capture import CaptureLease
from core.signal_statistics import range_statistics

#: How the statistics range names itself, mapped to the panel's language.
_RANGE_LABELS = {
    'between the cursors': '双光标之间',
    'in the visible range': '可见范围',
}

def _fmt(value: float) -> str:
    return f'{value:.6g}'


# ── Context capture ────────────────────────────────────────────────────────

def capture_context(window) -> AnalysisContext:
    """Snapshot the analysis context of a MainWindow: the selected rows of
    the plot's signal table (every plotted signal when none is selected),
    and the same range its statistics columns use — between the cursors
    when both are on, else the visible range, else the whole recording.
    """
    panel = getattr(window, 'plot_panel', None)
    path = getattr(window, 'measurement_path', None) or ''
    keys: list[str] = []
    if panel is not None:
        keys = list(panel.selected_keys()) or list(panel.plotted_keys())
    rng = panel.statistics_range() if panel is not None else None
    if rng is not None:
        t0, t1, source = float(rng[0]), float(rng[1]), rng[2]
        label = _RANGE_LABELS.get(source, source)
    else:
        t0 = t1 = None
        label = '全程'
    store = getattr(window, 'store', None)
    return AnalysisContext(measurement=Path(path).name if path else '',
                           keys=tuple(keys), t0=t0, t1=t1, range_label=label,
                           source_id=measurement_source_id(store) if store is not None else '')


# ── Local analyzer (pure, no Qt) ───────────────────────────────────────────

def analyze(store, context: AnalysisContext,
            cancel=None) -> AnalysisResult:
    """Read-only analysis of *context* against *store*. ``cancel`` is polled
    between signals; when it fires the result comes back with whatever was
    found so far and ``cancelled=True``."""
    findings: list[Finding] = []
    keys = context.keys
    if not keys:
        findings.append(Finding(
            '缺失', '未选择任何信号：在信号表选中几行（或先绘制信号），'
                    '再点「捕获当前上下文」。'))
        return AnalysisResult(context, tuple(findings))
    if len(keys) > MAX_ANALYSIS_KEYS:
        findings.append(Finding(
            '缺失', f'上下文含 {len(keys)} 条信号，本地分析仅分析前 '
                    f'{MAX_ANALYSIS_KEYS} 条（首版上限）。'))
        keys = keys[:MAX_ANALYSIS_KEYS]
    cancelled = False
    for key in keys:
        if cancel is not None and cancel():
            cancelled = True
            break
        findings.extend(_analyze_signal(store, key, context))
    return AnalysisResult(context, tuple(findings), cancelled=cancelled)


def _analyze_signal(store, key: str, context: AnalysisContext) -> list[Finding]:
    name = key.rsplit('::', 1)[-1]

    def evidence(start, end, label):
        return Evidence(key, start, end, label, source_id=context.source_id)

    series = store.get_series(key) if store is not None else None
    if series is None:
        return [Finding(
            '缺失', f'{key} 不在当前测量中（可能来自其他日志或已删除的计算信号）。')]
    ts = series.numpy_timestamps()
    vs = series.numpy_values()
    unit = series.unit or ''
    t0, t1 = context.t0, context.t1
    if t0 is None or t1 is None:
        if not len(ts):
            return [Finding('缺失', f'{name} 无任何样本。')]
        t0, t1 = float(ts[0]), float(ts[-1])

    if series.has_labels:
        count = int(((ts >= t0) & (ts <= t1)).sum())
        return [Finding(
            '现象',
            f'{name} 为枚举/文本信号，区间内 {count} 个样本；本地数值分析暂不支持标签信号。',
            evidence(t0, t1, f'{name} 全区间'))]

    window = (ts >= t0) & (ts <= t1)
    if not window.any():
        held = range_statistics(ts, vs, t0, t1, hold=True)
        if held is not None and held.samples == 0:
            return [Finding(
                '缺失',
                f'{name} 在 {_fmt(t0)}–{_fmt(t1)} s 内无采样；区间外最后采样保持为 '
                f'{_fmt(held.mean)} {unit}。',
                evidence(t0, t1, f'{name} 无采样区间'))]
        return [Finding('缺失', f'{name} 在 {_fmt(t0)}–{_fmt(t1)} s 内无采样。',
                        evidence(t0, t1, f'{name} 无采样区间'))]

    stats = range_statistics(ts, vs, t0, t1)
    tw = ts[window]
    vw = vs[window]
    span_ev = evidence(float(tw[0]), float(tw[-1]), f'{name} 统计区间')
    findings = [Finding(
        '现象',
        f'{name}：{stats.samples} 个样本，最小 {_fmt(stats.minimum)} ~ '
        f'最大 {_fmt(stats.maximum)} {unit}，均值 {_fmt(stats.mean)}，'
        f'标准差 {_fmt(stats.std)}。',
        span_ev)]

    if stats.samples >= 2 and stats.std == 0.0:
        findings[0] = Finding(
            '现象',
            f'{name}：区间内恒定于 {_fmt(stats.mean)} {unit}（{stats.samples} 个样本）。',
            span_ev)
    elif len(vw) >= 2:
        deltas = np.diff(vw)
        idx = int(np.argmax(np.abs(deltas)))
        step = float(deltas[idx])
        if step != 0.0:
            t_before, t_after = float(tw[idx]), float(tw[idx + 1])
            pad = (t_after - t_before) or max((t1 - t0) * 0.01, 1e-3)
            findings.append(Finding(
                '现象',
                f'{name}：最大单步变化 {step:+.6g} {unit}'
                f'（{_fmt(float(vw[idx]))} → {_fmt(float(vw[idx + 1]))}）'
                f'@ {_fmt(t_after)} s。',
                evidence(t_before - pad, t_after + pad, f'{name} 单步变化')))

    if len(tw) >= 3:
        dts = np.diff(tw)
        if (dts > 0).all():
            median = float(np.median(dts))
            largest = float(dts.max())
            if median > 0.0 and largest > 3.0 * median:
                gi = int(np.argmax(dts))
                findings.append(Finding(
                    '现象',
                    f'{name}：采样间隔异常，最大 {_fmt(largest)} s'
                    f'（约 {largest / median:.1f}× 中位 {_fmt(median)} s）'
                    f'@ {_fmt(float(tw[gi + 1]))} s。',
                    evidence(float(tw[gi]), float(tw[gi + 1]), f'{name} 采样间隔')))
    return findings


# ── Worker thread ──────────────────────────────────────────────────────────

class _AnalysisWorker(QObject):
    finished = Signal(object)
    failed = Signal(object, str)

    def __init__(self, store, context: AnalysisContext, cancel_check) -> None:
        super().__init__()
        self._store = store
        self._context = context
        self._cancel_check = cancel_check

    @Slot()
    def run(self) -> None:
        try:
            result = analyze(self._store, self._context,
                             cancel=self._cancel_check)
        except Exception as exc:  # the panel reports it; the app stays up
            self.failed.emit(self._context, f'{type(exc).__name__}: {exc}')
            return
        self.finished.emit(result)


# ── Agent worker (owns the Codex session off the GUI thread) ───────────────

@dataclass(frozen=True)
class _DesktopRequest:
    cancel: threading.Event
    question: str = ''
    scope: str = ''
    thread_id: str = ''
    project: DesktopProject | None = None


class _DesktopWorker(QObject):
    answer_ready = Signal(object, object)
    failed = Signal(object, str)
    start_request = Signal(object)

    def __init__(self, desktop: DesktopChats) -> None:
        super().__init__()
        self._desktop = desktop
        self.start_request.connect(self.run)

    @Slot(object)
    def run(self, request: _DesktopRequest) -> None:
        cancelled = request.cancel.is_set
        try:
            if cancelled():
                raise Cancelled('已取消。')
            if request.thread_id:
                result = self._desktop.read(request.thread_id, cancelled)
            elif request.scope:
                result = self._desktop.create(request.scope, request.question, cancelled,
                                              project=request.project)
            else:
                result = self._desktop.projects(cancelled)
            if cancelled():
                raise Cancelled('已取消。')
        except BridgeError as exc:
            self.failed.emit(request, str(exc))
        except Exception as exc:
            self.failed.emit(request, f'请求失败（{type(exc).__name__}）。')
        else:
            self.answer_ready.emit(request, result)


# ── Panel ──────────────────────────────────────────────────────────────────

class AssistantPanel(QWidget):
    """The assistant's content widget; the MainWindow docks it. Shows the
    captured context, runs the local analyzer off the GUI thread, lists
    findings and evidence, and emits :attr:`evidenceActivated` for the
    window to zoom the plot. :attr:`recaptureRequested` fires from the
    capture button."""

    PRIVACY_NOTE = '本地分析的数据留在本机'
    evidenceActivated = Signal(object)
    recaptureRequested = Signal()
    shareRequested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None, *, project_home: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('AssistantPanel')
        self._context: AnalysisContext | None = None
        self._running = False
        self._cancelled = False
        self._thread: QThread | None = None
        self._worker: _AnalysisWorker | None = None
        self._last_store = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel('本次分析上下文'))
        self.context_label = QLabel('（尚未捕获）')
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)

        buttons = QHBoxLayout()
        self.capture_btn = QPushButton('捕获当前上下文')
        self.capture_btn.setToolTip('把当前选中的信号与时间范围（双光标之间，'
                                    '否则可见范围）固定为本次分析的上下文')
        self.capture_btn.clicked.connect(self.recaptureRequested.emit)
        self.run_btn = QPushButton('开始分析')
        self.run_btn.setToolTip('对上下文里的信号做本地只读分析')
        self.run_btn.clicked.connect(self._on_run_clicked)
        self.stop_btn = QPushButton('停止')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)
        buttons.addWidget(self.capture_btn)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.stop_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel('空闲')
        layout.addWidget(self.status_label)

        layout.addWidget(QLabel('分析结果'))
        self.findings_box = QTextEdit()
        self.findings_box.setReadOnly(True)
        layout.addWidget(self.findings_box, stretch=2)

        layout.addWidget(QLabel('证据（双击定位到波形）'))
        self.evidence_list = QListWidget()
        self.evidence_list.itemActivated.connect(self._evidence_activated)
        layout.addWidget(self.evidence_list, stretch=1)

        self._desktop = (DesktopChats(project_home / 'codex-captures')
                         if project_home is not None and sys.platform == 'win32' else None)
        self._shared_lease: CaptureLease | None = None
        self._shared_chat_id = ''
        self._shared_question = ''
        self._desktop_request: _DesktopRequest | None = None
        self._desktop_thread: QThread | None = None
        self._desktop_worker: _DesktopWorker | None = None
        self._shutting_down = False
        if self._desktop is not None:
            self._project_file = project_home / 'assistant-codex-project.txt'
            try:
                self._project_id = self._project_file.read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError):
                self._project_id = ''
            projects = QHBoxLayout()
            projects.addWidget(QLabel('Codex 项目'))
            self.codex_project_combo = QComboBox()
            self.codex_project_combo.setAccessibleName('Codex 项目')
            self.codex_project_combo.setPlaceholderText('请选择项目')
            self.codex_project_combo.currentIndexChanged.connect(self._change_codex_project)
            projects.addWidget(self.codex_project_combo, stretch=1)
            self.refresh_projects_btn = QPushButton('刷新项目')
            self.refresh_projects_btn.clicked.connect(self.refresh_codex_projects)
            projects.addWidget(self.refresh_projects_btn)
            layout.addLayout(projects)

            self.question_input = QPlainTextEdit()
            self.question_input.setPlaceholderText('例如：目标变了，执行器为什么没跟上？')
            self.question_input.setFixedHeight(52)
            layout.addWidget(self.question_input)

            share_buttons = QHBoxLayout()
            self.share_btn = QPushButton('在 Codex 中分析')
            self.share_btn.clicked.connect(self._on_share_clicked)
            self.read_chat_btn = QPushButton('刷新聊天')
            self.read_chat_btn.clicked.connect(self._read_shared_chat)
            self.revoke_btn = QPushButton('撤销取数')
            self.revoke_btn.clicked.connect(self._revoke_clicked)
            self.cancel_codex_btn = QPushButton('取消')
            self.cancel_codex_btn.clicked.connect(self._cancel_desktop)
            setup_btn = QPushButton('配置共享')
            setup_btn.setToolTip('复制注册 Osvanta 捕获工具的 PowerShell 命令')
            setup_btn.clicked.connect(self._copy_share_setup)
            for button in (self.share_btn, self.read_chat_btn, self.revoke_btn,
                           self.cancel_codex_btn, setup_btn):
                share_buttons.addWidget(button)
            layout.addLayout(share_buttons)
            layout.addWidget(QLabel('Codex 证据（双击定位到波形）'))
            self.codex_evidence_list = QListWidget()
            self.codex_evidence_list.itemActivated.connect(self._evidence_activated)
            layout.addWidget(self.codex_evidence_list, stretch=1)

        self.privacy_label = QLabel(self.PRIVACY_NOTE)
        self.privacy_label.setWordWrap(True)
        layout.addWidget(self.privacy_label)
        self._update_desktop_buttons()

    def _fill_codex_projects(self, projects) -> None:
        combo = self.codex_project_combo
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem('不归入项目')
            selected = 0 if not self._project_id else -1
            for project in projects:
                combo.addItem(project.name, project)
                index = combo.count() - 1
                combo.setItemData(index, '\n'.join(map(str, project.roots)), Qt.ItemDataRole.ToolTipRole)
                if project.id == self._project_id:
                    selected = index
            combo.setCurrentIndex(selected)
        finally:
            combo.blockSignals(blocked)
        self._show_project_path()
        self._update_desktop_buttons()

    def _show_project_path(self) -> None:
        project = self.codex_project_combo.currentData()
        self.codex_project_combo.setToolTip('\n'.join(map(str, project.roots)) if project else '')

    def _change_codex_project(self) -> None:
        project = self.codex_project_combo.currentData()
        self._project_id = project.id if project else ''
        self._show_project_path()
        if self._shared_chat_id:
            self.findings_box.clear()
        self.revoke_shared()
        try:
            self._project_file.parent.mkdir(parents=True, exist_ok=True)
            self._project_file.write_text(self._project_id + '\n', encoding='utf-8')
        except OSError:
            self.codex_status('项目已切换，无法保存下次启动的选择。')

    def refresh_codex_projects(self) -> None:
        if self._desktop is not None:
            self._start_desktop(_DesktopRequest(threading.Event()), '正在读取 Codex 项目…')

    def context(self) -> AnalysisContext | None:
        return self._context

    def set_context(self, context: AnalysisContext) -> None:
        if context == self._context:
            return
        self._cancelled = True
        self.revoke_shared()
        self.findings_box.clear()
        self.evidence_list.clear()
        self._context = context
        names = [key.rsplit('::', 1)[-1] for key in context.keys]
        if names:
            shown = '、'.join(names[:MAX_ANALYSIS_KEYS])
            if len(names) > MAX_ANALYSIS_KEYS:
                shown += f'（共 {len(names)} 条，仅使用前 {MAX_ANALYSIS_KEYS} 条）'
        else:
            shown = '无（选中信号表几行，或先绘制信号）'
        if context.t0 is not None and context.t1 is not None:
            span = (f'{_fmt(context.t0)} – {_fmt(context.t1)} s'
                    f'（{context.range_label}）')
        else:
            span = '全程'
        measurement = context.measurement or '（未命名测量）'
        self.context_label.setText(
            f'测量：{measurement}\n信号：{shown}\n范围：{span}')
        self.context_label.setToolTip('\n'.join(context.keys[:MAX_ANALYSIS_KEYS]))

    def is_running(self) -> bool:
        return self._running

    # ── Running ────────────────────────────────────────────────────────

    def _on_run_clicked(self) -> None:
        self.start_analysis(self._last_store)

    def start_analysis(self, store, synchronous: bool = False) -> None:
        """Analyse the captured context against *store*. ``synchronous``
        runs on the GUI thread (tests, very small stores); the default
        runs in a worker thread so the panel keeps a live 停止 button."""
        if self._running:
            return
        context = self._context
        if context is None or not context.keys:
            self.status_label.setText('请先捕获当前上下文（要有选中或已绘制的信号）。')
            return
        if store is None:
            self.status_label.setText('当前没有已解码的测量数据。')
            return
        self._last_store = store
        self._cancelled = False
        self._set_running(True)
        self.status_label.setText('分析中…')
        if synchronous:
            try:
                result = analyze(store, context)
            except Exception as exc:
                self._on_failed(context, f'{type(exc).__name__}: {exc}')
                return
            self._on_finished(result)
            return
        self._thread = QThread(self)
        self._worker = _AnalysisWorker(store, context,
                                       lambda: self._cancelled)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def stop_analysis(self) -> None:
        if not self._running:
            return
        self._cancelled = True
        self.status_label.setText('正在取消…')

    def shutdown(self, wait_ms: int = 5000) -> None:
        """Request thread cleanup and retain workers until they stop."""
        if not self._shutting_down:
            self._shutting_down = True
            self._cancelled = True
            self.revoke_shared()
            if self._desktop_request is not None:
                self._desktop_request.cancel.set()
            for thread in (self._thread, self._desktop_thread):
                if thread is not None:
                    thread.quit()
            self._update_desktop_buttons()
        for thread in (self._thread, self._desktop_thread):
            if thread is not None and wait_ms:
                thread.wait(wait_ms)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    # ── Results ────────────────────────────────────────────────────────

    def _on_finished(self, result: AnalysisResult) -> None:
        self._set_running(False)
        if self._shutting_down or result.context is not self._context:
            return
        if result.cancelled or self._cancelled:
            self.status_label.setText(
                f'已取消（已得 {len(result.findings)} 条发现，'
                f'{len(result.evidence_rows)} 条证据）')
        else:
            self.status_label.setText(
                f'完成：{len(result.findings)} 条发现，'
                f'{len(result.evidence_rows)} 条证据')
        self._render_result(result)

    def _on_failed(self, context: AnalysisContext, message: str) -> None:
        self._set_running(False)
        if not self._shutting_down and context is self._context:
            self.status_label.setText(f'失败：{message}')

    def _render_result(self, result: AnalysisResult) -> None:
        lines = [f'【{finding.kind}】{finding.text}'
                 for finding in result.findings]
        self.findings_box.setPlainText('\n'.join(lines) or '（无发现）')
        self.evidence_list.clear()
        for ev in result.evidence_rows:
            name = ev.key.rsplit('::', 1)[-1]
            item = QListWidgetItem(
                f'{name} · {_fmt(ev.t0)}–{_fmt(ev.t1)} s · {ev.text}')
            item.setData(Qt.ItemDataRole.UserRole, ev)
            self.evidence_list.addItem(item)

    def _evidence_activated(self, item: QListWidgetItem) -> None:
        ev = item.data(Qt.ItemDataRole.UserRole)
        if ev is not None:
            self.evidenceActivated.emit(ev)

    # ── Agent questions ────────────────────────────────────────────────

    def _start_desktop(self, request: _DesktopRequest, status: str) -> None:
        if self._shutting_down or self._desktop_request is not None:
            return
        if self._desktop_worker is None:
            self._desktop_thread = QThread(self)
            self._desktop_worker = _DesktopWorker(self._desktop)
            self._desktop_worker.moveToThread(self._desktop_thread)
            self._desktop_worker.answer_ready.connect(self._desktop_answer)
            self._desktop_worker.failed.connect(self._desktop_failed)
            self._desktop_thread.finished.connect(self._desktop_worker.deleteLater)
            self._desktop_thread.start()
        self._desktop_request = request
        self._update_desktop_buttons()
        self.status_label.setText(status)
        self._desktop_worker.start_request.emit(request)

    def codex_status(self, message: str) -> None:
        self.status_label.setText(f'Codex：{message}')

    def _update_desktop_buttons(self) -> None:
        if self._desktop is None:
            return
        busy = self._desktop_request is not None
        idle = not busy and not self._shutting_down
        self.codex_project_combo.setEnabled(idle)
        self.refresh_projects_btn.setEnabled(idle)
        self.share_btn.setEnabled(idle and (bool(self._shared_chat_id)
                                            or self.codex_project_combo.currentIndex() >= 0))
        self.share_btn.setText('打开 Codex 聊天' if self._shared_chat_id else '在 Codex 中分析')
        self.read_chat_btn.setVisible(bool(self._shared_chat_id))
        self.read_chat_btn.setEnabled(idle)
        self.revoke_btn.setVisible(self._shared_lease is not None and not busy)
        self.cancel_codex_btn.setVisible(busy and not self._shutting_down)
        self.privacy_label.setText('Codex 可读取本次授权的信号和时间范围。'
                                   if self._shared_lease is not None else self.PRIVACY_NOTE)

    def _copy_share_setup(self) -> None:
        QApplication.clipboard().setText(self._desktop.setup_command())
        self.codex_status('已复制 PowerShell 配置命令。运行后，在 Codex 设置 → MCP 服务器中点击重启。')

    def _on_share_clicked(self) -> None:
        if self._desktop_request is not None or self._shutting_down:
            return
        if self._shared_chat_id:
            self._open_shared_chat()
            return
        if self.codex_project_combo.currentIndex() < 0:
            self.codex_status('请选择 Codex 项目。')
            return
        context = self._context
        project = self.codex_project_combo.currentData()
        question = self.question_input.toPlainText().strip()
        if context is None or not context.keys:
            self.codex_status('请先捕获信号和时间范围。')
            return
        if not question or len(question) > MAX_QUESTION:
            self.codex_status(f'请输入 1 至 {MAX_QUESTION} 字的分析问题。')
            return
        prompt = QMessageBox(self)
        prompt.setWindowTitle('共享到 Codex 桌面端')
        prompt.setTextFormat(Qt.TextFormat.PlainText)
        prompt.setText('允许 Codex 模型读取当前捕获范围内的信号、单位和样本。\n'
                       '问题和工具结果可保存在 Codex 聊天历史中。撤销取数会关闭后续访问。')
        project_label = project.name if project else '不归入项目'
        roots = '\n'.join(map(str, project.roots)) if project else ''
        destination = f'Codex 项目：{project_label}' + (f'\n{roots}' if roots else '')
        prompt.setInformativeText(f'{destination}\n\n{self.context_label.text()}')
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        prompt.setDefaultButton(QMessageBox.StandardButton.Cancel)
        prompt.button(QMessageBox.StandardButton.Yes).setText('授权并打开')
        if (prompt.exec() == QMessageBox.StandardButton.Yes and context is self._context
                and project == self.codex_project_combo.currentData() and not self._shutting_down
                and self._desktop_request is None):
            self.shareRequested.emit(question, project)

    def begin_shared(self, snapshot, question: str, project: DesktopProject | None) -> None:
        if self._desktop is None or self._desktop_request is not None or self._shutting_down:
            return
        if self._shared_lease is not None:
            self.revoke_shared()
        try:
            self._shared_lease = CaptureLease(AnalysisTools(snapshot), self._desktop.directory)
        except OSError:
            self.codex_status('无法开启本机捕获工具。')
            return
        self._start_desktop(_DesktopRequest(threading.Event(), question=question,
                                          scope=self._shared_lease.scope, project=project),
                            '正在创建 Codex 聊天…')

    def _open_shared_chat(self) -> None:
        if QDesktopServices.openUrl(QUrl(chat_url(self._shared_chat_id))):
            self.codex_status('已打开 Codex 聊天，可在桌面端继续。')
        else:
            self.codex_status('无法打开 Codex 桌面端。')

    def _read_shared_chat(self) -> None:
        if self._shared_chat_id:
            self._start_desktop(_DesktopRequest(threading.Event(), thread_id=self._shared_chat_id),
                                '正在读取 Codex 聊天…')

    def revoke_shared(self) -> None:
        lease, self._shared_lease = self._shared_lease, None
        if lease is not None:
            lease.close()
        self._shared_chat_id = ''
        self._shared_question = ''
        request = self._desktop_request
        if request is not None and (request.scope or request.thread_id):
            request.cancel.set()
        if self._desktop is not None:
            self.codex_evidence_list.clear()
        self._update_desktop_buttons()

    def _revoke_clicked(self) -> None:
        self.revoke_shared()
        self.codex_status('已撤销本次取数权限。')

    def _cancel_desktop(self) -> None:
        request = self._desktop_request
        if request is not None:
            request.cancel.set()
            if request.scope:
                self.revoke_shared()
            self.codex_status('正在取消…')

    def _finish_desktop(self, request: _DesktopRequest) -> bool:
        if request is not self._desktop_request:
            return False
        self._desktop_request = None
        self._update_desktop_buttons()
        return not request.cancel.is_set() and not self._shutting_down

    def _desktop_answer(self, request: _DesktopRequest, answer) -> None:
        if not self._finish_desktop(request):
            return
        if request.thread_id:
            transcript, ids = answer
            evidence = self._shared_lease.evidence(ids) if self._shared_lease else ()
            self.findings_box.setPlainText(
                f'【从 Osvanta 发起】\n{self._shared_question}\n\n'
                + (transcript or '（聊天暂无回复）'))
            self.codex_evidence_list.clear()
            for ev in evidence:
                if isinstance(ev, Evidence):
                    name = ev.key.rsplit('::', 1)[-1]
                    item = QListWidgetItem(
                        f'[{ev.evidence_id}] {name} · {_fmt(ev.t0)}–{_fmt(ev.t1)} s · {ev.text}')
                    item.setData(Qt.ItemDataRole.UserRole, ev)
                    self.codex_evidence_list.addItem(item)
            self.codex_status('已刷新 Codex 聊天。')
        elif request.scope:
            self._shared_chat_id = answer
            self._shared_question = request.question
            self._update_desktop_buttons()
            self._open_shared_chat()
        else:
            self._fill_codex_projects(answer)
            self.codex_status('项目列表已刷新。')

    def _desktop_failed(self, request: _DesktopRequest, message: str) -> None:
        if request is not self._desktop_request:
            return
        finished = self._finish_desktop(request)
        if request.scope:
            self.revoke_shared()
        if not self._shutting_down:
            if finished:
                self.codex_status(message)
            elif self.status_label.text() == 'Codex：正在取消…':
                self.codex_status('已取消。')

    def invalidate_context(self, reason: str) -> None:
        """Retire the old measurement, scope and all its results."""
        self._cancelled = True
        self._last_store = None
        self._context = None
        self.context_label.setText('（尚未捕获）')
        self.context_label.setToolTip('')
        self.evidence_list.clear()
        self.findings_box.clear()
        self.revoke_shared()
        self.status_label.setText(f'{reason}，请重新捕获上下文。')
