import pytest

from milas import app as milas_app


class _FakeFrame:
    def __init__(self, parent, **options):
        self.parent = parent
        self.options = options
        self.children = []
        self.pack_options = None
        self.pack_propagate_value = None

    def configure(self, **options):
        self.options.update(options)

    def pack(self, **options):
        self.pack_options = options

    def pack_propagate(self, value):
        self.pack_propagate_value = value

    def winfo_children(self):
        return self.children


class _FakeCanvas:
    def __init__(self, parent, **options):
        self.parent = parent
        self.options = options
        self.bindings = {}
        self.after_calls = []
        self.pack_options = None
        self.items = {}
        self.coordinates = {}
        self.grabbed = False
        self.focused = False
        parent.children.append(self)

    def bind(self, sequence, callback, add=None):
        self.bindings[sequence] = callback

    def configure(self, **options):
        self.options.update(options)

    def create_text(self, x, y, **options):
        item = len(self.items) + 1
        self.items[item] = options
        self.coordinates[item] = (x, y)
        return item

    def itemconfigure(self, item, **options):
        self.items[item].update(options)

    def coords(self, item, x, y):
        self.coordinates[item] = (x, y)

    def winfo_width(self):
        return 200

    def winfo_height(self):
        return 20

    def focus_set(self):
        self.focused = True

    def grab_set(self):
        self.grabbed = True

    def grab_release(self):
        self.grabbed = False

    def after_idle(self, callback):
        self.after_calls.append(callback)

    def pack(self, **options):
        self.pack_options = options


class _FakeFont:
    def metrics(self, metric):
        assert metric == "linespace"
        return 18


@pytest.mark.parametrize(
    "label",
    [
        "Today",  # Reference Parameters
        "Today",  # Comparison Parameters
        "Minimum Eccentricity",
        "Maximum Eccentricity",
        "Minimum Obliquity",
        "Maximum Obliquity",
        "Opposite Precession Phase",
    ],
)
def test_sidebar_buttons_have_outer_border_and_stable_states(monkeypatch, label):
    monkeypatch.setattr(milas_app.tk, "Canvas", _FakeCanvas)
    monkeypatch.setattr(milas_app.tk, "Frame", _FakeFrame)
    window = milas_app.MainWindow.__new__(milas_app.MainWindow)
    window._button_font = _FakeFont()
    commands = []
    border = window._button(None, label, lambda: commands.append(label))
    button = border.winfo_children()[0]
    border.pack(fill="x")

    assert border.pack_options == {"fill": "x"}
    assert border.options["bg"] == milas_app.BUTTON_BORDER
    assert border.options["bd"] == 0
    assert border.options["highlightthickness"] == 0
    assert border.options["height"] == 22
    assert border.pack_propagate_value is False
    assert button.pack_options == {
        "fill": "both", "expand": True, "padx": 1, "pady": 1,
    }
    assert button.options["bg"] == milas_app.BUTTON_STATE_COLORS["normal"][0]
    assert button.options["takefocus"] is True
    assert button.options["bd"] == 0
    assert button.options["highlightthickness"] == 0
    label_item, label_options = next(iter(button.items.items()))
    assert label_options["text"] == label
    assert label_options["font"] is window._button_font
    assert label_options["fill"] == milas_app.BUTTON_STATE_COLORS["normal"][1]
    assert label_options["anchor"] == "center"
    assert button.pack_options == {
        "fill": "both", "expand": True, "padx": 1, "pady": 1,
    }
    button.bindings["<Configure>"](None)
    assert button.coordinates[label_item] == (100, 10)
    fixed_size_options = (border.options["height"], button.pack_options)

    def assert_fixed_height():
        assert border.options["height"] == 22

    button.bindings["<Enter>"](None)
    assert_fixed_height()
    assert button.options["bg"] == milas_app.BUTTON_STATE_COLORS["hover"][0]
    assert button.items[label_item]["fill"] == milas_app.BUTTON_STATE_COLORS["hover"][1]
    assert border.options["bg"] == milas_app.BUTTON_HOVER_BORDER
    button.after_calls.pop(0)()
    button.bindings["<ButtonPress-1>"](type("Event", (), {})())
    assert_fixed_height()
    assert button.options["bg"] == milas_app.BUTTON_PRESSED_BG
    assert button.items[label_item]["fill"] == milas_app.BUTTON_STATE_COLORS["pressed"][1]
    assert border.options["bg"] == milas_app.BUTTON_BORDER
    assert button.focused is True
    assert button.grabbed is True
    button.bindings["<FocusIn>"](None)
    assert_fixed_height()
    assert border.options["bg"] == milas_app.BUTTON_BORDER
    button.after_calls.pop(0)()
    button.bindings["<ButtonRelease-1>"](type("Event", (), {"x": 50, "y": 10})())
    assert button.options["bg"] == milas_app.BUTTON_STATE_COLORS["hover"][0]
    assert button.grabbed is False
    assert len(button.after_calls) == 1
    button.after_calls.pop(0)()
    assert commands == [label]
    assert_fixed_height()
    assert border.options["bg"] == milas_app.BUTTON_HOVER_BORDER
    button.bindings["<Leave>"](None)
    assert button.options["bg"] == milas_app.BUTTON_STATE_COLORS["normal"][0]
    assert border.options["bg"] == milas_app.BUTTON_BORDER

    button.bindings["<FocusIn>"](None)
    assert border.options["bg"] == milas_app.BUTTON_FOCUS
    assert "<KeyPress-space>" not in button.bindings
    assert "<KeyRelease-space>" not in button.bindings

    button.bindings["<KeyPress-Return>"](None)
    assert button.options["bg"] == milas_app.BUTTON_PRESSED_BG
    button.bindings["<KeyRelease-Return>"](None)
    button.after_calls.pop(0)()
    assert button.options["bg"] == milas_app.BUTTON_STATE_COLORS["normal"][0]
    assert commands == [label, label]
    assert border.options["bg"] == milas_app.BUTTON_FOCUS
    button.bindings["<FocusOut>"](None)
    assert border.options["bg"] == milas_app.BUTTON_BORDER

    assert (border.options["height"], button.pack_options) == fixed_size_options


class _SliderCanvasStub:
    def __init__(self):
        self._focused = True
        self._hovered = False
        self._dragging = True
        self.lines = []
        self.ovals = []

    def winfo_exists(self):
        return True

    def winfo_width(self):
        return 100

    def winfo_height(self):
        return 26

    def _track_bounds(self):
        return 9.0, 91.0

    def _value_x(self):
        return 50.0

    def delete(self, _tag):
        self.lines.clear()
        self.ovals.clear()

    def create_line(self, *coords, **options):
        self.lines.append((coords, options))

    def create_oval(self, *coords, **options):
        self.ovals.append((coords, options))


def test_focused_slider_draws_no_extra_handle_focus_ring():
    slider = _SliderCanvasStub()
    milas_app.ParameterSlider._redraw(slider)

    # Focus still highlights the active track, but draws only the normal handle.
    assert slider.lines[1][1]["fill"] == "#347eae"
    assert len(slider.ovals) == 1
    coords, options = slider.ovals[0]
    assert coords == (43.5, 6.5, 56.5, 19.5)
    assert options["outline"] == "#f7f8f9"
