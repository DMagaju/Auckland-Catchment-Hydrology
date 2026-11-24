# -*- coding: utf-8 -*-
"""
Helper for styling native sub-dialogs this plugin doesn't construct
directly (e.g. QgsProjectionSelectionWidget's CRS browser).

WHY THIS EXISTS: on Windows with Qt6's native style, various QGIS
sub-dialogs (CRS selector, etc.) render with a dark/unreadable background
because native-style painting mostly ignores QPalette/QSS for these
controls unless the dialog itself is explicitly given a stylesheet.
Those dialogs are created internally by QGIS/Qt — this plugin never gets
a direct handle to construct or pre-style them — so the only hook
available is to catch the dialog's own Show event and style it then.

Ported from the TUFLOW Model Builder plugin's dialog_style.py (same
author, same bug, same fix) — see that file's docstring / PLUGINS.md
"Dark-Theme Widget Fixes" for the full history of failed approaches
(class-name matching, window-title matching, before/after open-dialog
diffing) that this Show-event approach replaces.
"""

from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtWidgets import QApplication, QDialog, QWidget

try:
    _ShowEvent = QEvent.Type.Show
except AttributeError:
    _ShowEvent = QEvent.Show                         # type: ignore

try:
    _MousePress = QEvent.Type.MouseButtonPress
except AttributeError:
    _MousePress = QEvent.MouseButtonPress            # type: ignore

DIALOG_STYLE = """
QDialog, QWidget, QFrame, QAbstractScrollArea {
    background-color: #FAFAFA; color: #212121;
}
QListView, QTreeView, QTableView {
    background-color: white; color: #212121;
    alternate-background-color: #F5F5F5;
    selection-background-color: #BBDEFB;
    selection-color: #0D47A1;
}
QHeaderView::section {
    background-color: #ECEFF1; color: #212121;
    border: 1px solid #CFD8DC; padding: 3px 6px;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: white; color: #212121;
    border: 1px solid #B0BEC5; border-radius: 3px; padding: 2px 4px;
}
QComboBox QAbstractItemView {
    background-color: white; color: #212121;
    selection-background-color: #BBDEFB; selection-color: #0D47A1;
    border: 1px solid #B0BEC5;
}
QGroupBox {
    color: #212121; border: 1px solid #CFD8DC;
    border-radius: 4px; margin-top: 8px; padding-top: 6px;
}
QGroupBox::title { color: #212121; subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLabel { color: #212121; background: transparent; }
QCheckBox, QRadioButton { color: #212121; background: transparent; }
QPushButton {
    background-color: #1565C0; color: white; border: none;
    border-radius: 4px; padding: 5px 16px;
}
QPushButton:hover   { background-color: #1976D2; }
QPushButton:pressed { background-color: #0D47A1; }
QTabBar::tab { background: #ECEFF1; color: #212121; padding: 4px 10px;
    border: 1px solid #CFD8DC; border-bottom: none; }
QTabBar::tab:selected { background: white; color: #1565C0; font-weight: bold; }
QSplitter::handle { background-color: #ECEFF1; }
QScrollBar:vertical   { background: #F5F5F5; width: 12px; }
QScrollBar:horizontal { background: #F5F5F5; height: 12px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #B0BEC5; border-radius: 3px; min-length: 20px;
}
"""


class _PopupCatcher(QObject):
    """One-shot QApplication event filter: styles the next QDialog Show
    event it sees, then schedules its own removal via `on_caught`."""

    def __init__(self, style, on_caught):
        super().__init__()
        self._style = style
        self._on_caught = on_caught

    def eventFilter(self, obj, event):
        try:
            if event.type() == _ShowEvent and isinstance(obj, QDialog) and obj.isVisible():
                obj.setStyleSheet(self._style)
                # Defer — see the reentrancy note on watch_for_popup(). Never
                # mutate the app's filter list synchronously from inside a
                # call Qt is still using that same list to dispatch.
                QTimer.singleShot(0, self._on_caught)
        except Exception:
            pass
        return False


_active = []  # keeps catchers/watchers/timers alive while installed


def style_native_popup(style=DIALOG_STYLE, timeout_ms=4000):
    """
    Style the next QDialog that becomes visible, for a short window right
    after this call — use it immediately before/at the point of an action
    known to trigger a third-party dialog this plugin doesn't construct
    directly (e.g. QgsProjectionSelectionWidget's CRS browser), since that
    dialog is created internally by QGIS and we never get a direct handle
    to it.

    Catches the dialog via its own Show event — NOT by class name, window
    title, or diffing which QDialogs are newly open. All three fail in
    practice: class names vary across QGIS builds; window titles are free
    text with no reliable fixed substring (the CRS browser's actual title,
    "Coordinate Reference System Selector", contains neither "projection"
    nor "crs"); and a before/after diff of open QDialogs assumes the
    triggering action always constructs a fresh dialog instance, which
    isn't guaranteed — some widgets construct their internal dialog once
    and just re-show() the same cached instance on every use, which a
    "wasn't already open" diff can never catch past the first time.
    Watching Show directly sidesteps all three failure modes. Self-removes
    after catching one dialog, or after `timeout_ms` if none appears, so
    it never lingers as a permanent app-wide filter.
    """
    app = QApplication.instance()
    if app is None:
        return

    entry = {}

    def _cleanup():
        try:
            app.removeEventFilter(entry['catcher'])
            entry['timer'].stop()
        finally:
            if entry in _active:
                _active.remove(entry)

    entry['catcher'] = _PopupCatcher(style, _cleanup)
    entry['timer'] = QTimer()
    entry['timer'].setSingleShot(True)
    entry['timer'].timeout.connect(_cleanup)
    _active.append(entry)

    app.installEventFilter(entry['catcher'])
    entry['timer'].start(timeout_ms)


class _ClickWatcher(QObject):
    """Narrowly-scoped event filter installed directly on a trigger
    widget and its (current) children — see watch_for_popup()."""

    def __init__(self, style):
        super().__init__()
        self._style = style

    def eventFilter(self, obj, event):
        try:
            if event.type() == _MousePress:
                # Defer — arming a catcher calls installEventFilter, and
                # doing that synchronously from inside an eventFilter call
                # that Qt is still dispatching through is unsafe reentrant
                # mutation of the filter list — see watch_for_popup().
                QTimer.singleShot(0, lambda: style_native_popup(style=self._style))
        except Exception:
            pass
        return False


def watch_for_popup(trigger_widget, style=DIALOG_STYLE):
    """
    Arm a watcher on `trigger_widget`: any mouse-button press on it, or on
    one of its (current) child widgets, arms a fresh style_native_popup()
    catch for whatever dialog that click opens next.

    Installed LOCALLY on trigger_widget and its children — NOT
    QApplication-wide, and NOT a permanent app-wide click filter. An
    app-wide click filter fires on every mouse press across the entire
    application (every map-canvas tool click, every other dock, every
    menu), and arming a catcher from inside that filter's eventFilter()
    would call installEventFilter() on the app while Qt is still
    iterating that same filter list to dispatch the current event —
    unsafe reentrant mutation. Scoping installation to this one widget's
    own subtree avoids that, and every list-mutating call is deferred via
    QTimer.singleShot(0) so it always runs after the current event's
    dispatch has fully finished.

    Call once, right after constructing the trigger widget, so its
    children already exist to be covered.
    """
    watcher = _ClickWatcher(style)
    trigger_widget.installEventFilter(watcher)
    for child in trigger_widget.findChildren(QWidget):
        child.installEventFilter(watcher)
    _active.append(watcher)
    return watcher
