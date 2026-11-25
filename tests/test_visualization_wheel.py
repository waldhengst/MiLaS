from types import SimpleNamespace
from unittest.mock import Mock

from milas.app import MainWindow


class _ScrollTarget:
    def __init__(self, x, y, width, height):
        self.bounds = (x, y, width, height)
        self.scrolls = []

    def winfo_rootx(self):
        return self.bounds[0]

    def winfo_rooty(self):
        return self.bounds[1]

    def winfo_width(self):
        return self.bounds[2]

    def winfo_height(self):
        return self.bounds[3]

    def scroll(self, units):
        self.scrolls.append(units)

    def yview_scroll(self, units, _what):
        self.scrolls.append(units)

    def cget(self, option):
        assert option == "scrollregion"
        return self.scrollregion


def _window_for_visualization_scroll(content_height, viewport_height):
    window = MainWindow.__new__(MainWindow)
    window.sidebar_scroll = _ScrollTarget(0, 0, 300, 700)
    window.visualization_scroll = _ScrollTarget(320, 0, 800, 700)
    window.visualization_canvas = _ScrollTarget(320, 0, 800, viewport_height)
    window.visualization_canvas.scrollregion = f"0 0 800 {content_height}"
    return window


def _wheel_event(x=500, y=300):
    return SimpleNamespace(x_root=x, y_root=y, delta=-120, num=None)


def test_hidden_visualization_does_not_scroll_for_rounding_only_overflow():
    window = _window_for_visualization_scroll(701, 700)

    assert window._on_mousewheel(_wheel_event()) == "break"
    assert window.visualization_canvas.scrolls == []


def test_visualization_wheel_scrolls_when_content_genuinely_overflows():
    window = _window_for_visualization_scroll(900, 700)

    assert window._on_mousewheel(_wheel_event()) == "break"
    assert window.visualization_canvas.scrolls == [3]


def test_visualization_overflow_threshold_is_two_pixels():
    one_pixel = _window_for_visualization_scroll(701, 700)
    two_pixels = _window_for_visualization_scroll(702, 700)

    assert not one_pixel._visualization_has_overflow()
    assert two_pixels._visualization_has_overflow()


def test_widget_handler_stops_no_overflow_wheel_before_global_handler():
    window = _window_for_visualization_scroll(700, 700)
    window._on_mousewheel = Mock(return_value="break")

    assert window._on_visualization_mousewheel(_wheel_event()) == "break"
    window._on_mousewheel.assert_not_called()


def test_widget_handler_delegates_wheel_when_content_overflows():
    window = _window_for_visualization_scroll(900, 700)
    window._on_mousewheel = Mock(return_value="break")
    event = _wheel_event()

    assert window._on_visualization_mousewheel(event) == "break"
    window._on_mousewheel.assert_called_once_with(event)


def test_early_wheel_handler_is_bound_to_both_visualization_canvases():
    class Widget:
        def __init__(self):
            self.bindtags_value = ("widget", "Canvas", ".", "all")
            self.class_bindings = Widget.class_bindings

        class_bindings = {}

        def bind_class(self, tag, sequence, callback):
            self.class_bindings[(tag, sequence)] = callback

        def bindtags(self, tags=None):
            if tags is not None:
                self.bindtags_value = tags
            return self.bindtags_value

    window = MainWindow.__new__(MainWindow)
    window.visualization_canvas = Widget()
    window.canvas_widget = Widget()
    window._bind_visualization_wheel_events()

    expected = {"<MouseWheel>", "<Button-4>", "<Button-5>"}
    tag = window.visualization_canvas.bindtags_value[0]
    assert tag.startswith("MiLaSVisualizationWheelGuard")
    for widget in (window.visualization_canvas, window.canvas_widget):
        assert widget.bindtags_value[0] == tag
    assert {
        sequence for bound_tag, sequence in window.visualization_canvas.class_bindings
        if bound_tag == tag
    } == expected
    assert all(callback == window._on_visualization_mousewheel
               for callback in window.visualization_canvas.class_bindings.values())
