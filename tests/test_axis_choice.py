"""Multi-axis: signals can be sent to an existing axis by choice.

Unit grouping is automatic and claims one axis strip per unit; a measurement
with many units eats the plot area. The signal context menu's 选择轴 sends
selected signals onto an axis that already exists instead. Only axes with a
visible signal are offered — an empty axis takes no space, so there is never
an empty one to choose.
"""
import numpy as np
import pytest

from core.signal_store import SignalSeries
from gui.plot_widget import PlotPanel


def series(name, unit):
    ts = np.linspace(0., 10., 200)
    return SignalSeries(channel=1, message_name='Msg', message_id=0x100,
                        signal_name=name, unit=unit, timestamps=ts,
                        values=np.sin(ts), raw_values=[], has_labels=False)


@pytest.fixture()
def three_units(panel, qapp):
    """°C, % and V plotted in multi-axis mode: two extra axes."""
    for name, unit in (('Temp', '°C'), ('Hum', '%'), ('Volt', 'V')):
        s = series(name, unit)
        panel.add_series(s.key, s)
    panel.set_multi_axis(True)
    qapp.processEvents()
    return panel


def test_signals_can_ride_an_existing_axis(three_units, qapp):
    panel = three_units
    keys = {p.series.signal_name: k for k, p in panel._items.items()}
    assert len(panel._extra_axes) == 2

    panel._set_axis_override([keys['Volt']], '°c')
    qapp.processEvents()

    assert len(panel._extra_axes) == 1           # the V strip is gone
    assert panel._items[keys['Volt']].unit_group == '°c'
    # both ride the same view box; the first group's is the plot's main one
    main_vb = panel.plot.plotItem.vb
    temp_vb = panel._items[keys['Temp']].view_box or main_vb
    volt_vb = panel._items[keys['Volt']].view_box or main_vb
    assert volt_vb is temp_vb

    panel._set_axis_override([keys['Volt']], None)
    qapp.processEvents()
    assert len(panel._extra_axes) == 2           # back to one axis per unit


def test_mixed_unit_axis_labels_list_every_unit(three_units):
    panel = three_units
    keys = {p.series.signal_name: k for k, p in panel._items.items()}
    panel._set_axis_override([keys['Volt']], '°c')
    label = panel._axis_group_label([keys['Temp'], keys['Volt']])
    assert label == '°C / V'


def test_own_axis_and_override_are_exclusive(three_units, qapp):
    panel = three_units
    key = next(iter(panel._items))
    panel._set_own_axis([key], True)
    assert panel._items[key].own_axis
    panel._set_axis_override([key], '°c')
    assert not panel._items[key].own_axis
    assert panel._items[key].axis_override == '°c'
    panel._set_own_axis([key], True)
    assert panel._items[key].own_axis
    assert panel._items[key].axis_override is None


def test_only_axes_with_a_visible_signal_are_offered(three_units, qapp):
    panel = three_units
    keys = {p.series.signal_name: k for k, p in panel._items.items()}
    offered = {ukey for ukey, _label, _members in panel._axis_choice_groups()}
    assert offered == {panel._effective_key(k) for k in keys.values()}

    panel._items[keys['Hum']].visible = False    # a hidden signal keeps no axis
    offered = {ukey for ukey, _label, _members in panel._axis_choice_groups()}
    assert panel._effective_key(keys['Hum']) not in offered
    assert len(offered) == 2


def test_axis_override_is_undoable(three_units, qapp):
    panel = three_units
    key = next(iter(panel._items))
    panel.discard_undo_history()
    panel._set_axis_override([key], '°c')
    assert panel._items[key].axis_override == '°c'
    panel.undo()
    qapp.processEvents()
    assert panel._items[key].axis_override is None
