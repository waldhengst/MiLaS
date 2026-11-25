from types import SimpleNamespace

from milas.app import MainWindow, RepeatingTimer


class _Timer:
    def __init__(self, active):
        self.active = active

    def isActive(self):
        return self.active


def test_paused_globes_are_blitted_after_a_canvas_redraw():
    class _Axis:
        def __init__(self):
            self.bbox = object()

    first_axis = _Axis()
    second_axis = _Axis()
    cached_boxes = []
    blit_calls = []

    class _Canvas:
        @staticmethod
        def copy_from_bbox(bbox):
            background = object()
            cached_boxes.append((bbox, background))
            return background

    window = SimpleNamespace(
        _canvas_resizing=False,
        _globe_backgrounds={},
        _rotation_timer=_Timer(active=False),
        globe_axes=[first_axis, second_axis],
        canvas=_Canvas(),
        _blit_globes=lambda: blit_calls.append("blit"),
    )

    MainWindow._cache_globe_backgrounds(window, None)

    assert len(window._globe_backgrounds) == 2
    assert len(cached_boxes) == 2
    assert blit_calls == ["blit"]
    assert window._rotation_timer.isActive() is False


def test_repeating_timer_does_not_restart_after_stop_in_callback():
    class _Root:
        def __init__(self):
            self.callbacks = {}
            self.next_id = 1

        def after(self, _delay, callback):
            callback_id = self.next_id
            self.next_id += 1
            self.callbacks[callback_id] = callback
            return callback_id

        def after_cancel(self, callback_id):
            self.callbacks.pop(callback_id, None)

        def run_next(self):
            callback_id, callback = self.callbacks.popitem()
            callback()

    root = _Root()
    calls = []
    timer = None

    def callback():
        calls.append("tick")
        timer.stop()

    timer = RepeatingTimer(root, 16, callback)
    timer.start()
    root.run_next()

    assert calls == ["tick"]
    assert not timer.isActive()
    assert root.callbacks == {}
