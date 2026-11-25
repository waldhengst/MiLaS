from types import SimpleNamespace

from milas.app import MainWindow


class _RotationTimer:
    def __init__(self, active):
        self.active = active
        self.start_count = 0
        self.stop_count = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.start_count += 1

    def stop(self):
        self.active = False
        self.stop_count += 1


def _window(rotation_active):
    window = MainWindow.__new__(MainWindow)
    window._space_pressed = False
    window._rotation_timer = _RotationTimer(rotation_active)
    return window


def test_space_toggles_rotation_once_per_keypress_even_when_key_repeats():
    window = _window(rotation_active=True)

    assert window._space_key_down(SimpleNamespace(widget=object())) == "break"
    assert window._rotation_timer.active is False
    assert window._rotation_timer.stop_count == 1
    assert window._space_pressed is True

    # Auto-repeat keypresses during one held SPACE press do not toggle again.
    assert window._space_key_down(SimpleNamespace(widget=object())) == "break"
    assert window._rotation_timer.active is False
    assert window._rotation_timer.stop_count == 1

    assert window._space_key_up(None) == "break"
    assert window._space_pressed is False
    assert window._space_key_down(SimpleNamespace(widget=object())) == "break"
    assert window._rotation_timer.active is True
    assert window._rotation_timer.start_count == 1


def test_space_shortcut_toggles_rotation_independent_of_event_widget():
    focused_button = object()
    another_widget = object()
    window = _window(rotation_active=False)

    window._space_key_down(SimpleNamespace(widget=focused_button))
    assert window._rotation_timer.active is True
    window._space_key_up(None)
    window._space_key_down(SimpleNamespace(widget=another_widget))
    assert window._rotation_timer.active is False
