# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The recording header leaves space for the signals, not gap controls."""
from __future__ import annotations

import numpy as np

from PySide6.QtWidgets import QDoubleSpinBox, QLabel

from core.signal_store import SignalSeries, SignalStore


def test_recording_header_has_no_gap_controls_after_load_and_restore(
        qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Compact recording header', 'test')
    store = SignalStore()
    store.base_ts = 1793278800.18
    store._series_by_key['temperature'] = SignalSeries(
        channel=1, message_name='Telemetry', message_id=1,
        signal_name='Temperature', unit='°C',
        timestamps=np.array([0., 1., 10., 11.]),
        values=np.array([20., 21., 30., 31.]))
    window.store = store
    try:
        window.plot_panel.add_series('temperature', store.get_series('temperature'))
        window.show()
        for timeout in (2., 0., 3.5):
            window.playback.apply_config({'gap_timeout': timeout, 'time_mode': 'wall'})
            window._prepare_playback()
            window.playback.seek(9.)
            qapp.processEvents()

            header = window.playback
            assert not [control for control in header.findChildren(QDoubleSpinBox)
                        if control.isVisible()]
            labels = [label.text() for label in header.findChildren(QLabel)
                      if label.isVisible()]
            assert not any('Stale after' in text or 'fresh' in text.lower()
                           for text in labels)
            assert header.time_label.isVisible()
            assert header.to_config()['gap_timeout'] == timeout
            row = window.plot_panel._row_lookup['temperature']
            value = window.plot_panel.table.item(row, 2).text()
            if timeout:
                assert value == 'Stale (8.0 s)'
            else:
                assert value == '21.000'
    finally:
        window.close()
        qapp.processEvents()
