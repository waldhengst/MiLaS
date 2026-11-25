from milas.app import VerticalScrollArea


class _Canvas:
    def __init__(self, width, height, content_height):
        self.width = width
        self.height = height
        self.content_height = content_height
        self.item_configurations = []
        self.options = {}

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def itemconfigure(self, _item_id, **kwargs):
        self.item_configurations.append(kwargs)

    def configure(self, **kwargs):
        self.options.update(kwargs)


class _Inner:
    def __init__(self, canvas):
        self.canvas = canvas

    def winfo_reqheight(self):
        return self.canvas.content_height

    def winfo_reqwidth(self):
        return self.canvas.width


class _Scrollbar:
    def __init__(self):
        self.grid_calls = []
        self.remove_calls = 0
        self.fractions = None

    def set(self, first, last):
        self.fractions = (first, last)

    def grid(self, **kwargs):
        self.grid_calls.append(kwargs)

    def grid_remove(self):
        self.remove_calls += 1


def _scroll_area(width=330, viewport_height=700, content_height=680):
    area = VerticalScrollArea.__new__(VerticalScrollArea)
    area.canvas = _Canvas(width, viewport_height, content_height)
    area.inner = _Inner(area.canvas)
    area.scrollbar = _Scrollbar()
    area.window_id = 1
    area._scroll_region_after_id = None
    area._scrollbar_visible = False
    area.update_idletasks = lambda: None
    return area


def _recalculate(area):
    VerticalScrollArea._update_scroll_region(area)


def test_sidebar_scrollbar_stays_hidden_through_maximize_and_restore():
    area = _scroll_area(viewport_height=700, content_height=680)

    _recalculate(area)  # initial windowed state
    area.canvas.height = 1100
    _recalculate(area)  # maximized
    area.canvas.height = 700
    _recalculate(area)  # restored

    assert area._scrollbar_visible is False
    assert area.scrollbar.grid_calls == []
    assert area.scrollbar.remove_calls == 0
    assert area.canvas.options["scrollregion"] == (0, 0, 330, 700)
    assert all(config.get("height") == 0 for config in area.canvas.item_configurations)


def test_sidebar_scrollbar_appears_only_for_real_overflow_and_uses_content_extent():
    area = _scroll_area(viewport_height=500, content_height=680)

    _recalculate(area)

    assert area._scrollbar_visible is True
    assert area.scrollbar.grid_calls == [{"row": 0, "column": 1, "sticky": "ns"}]
    assert area.canvas.options["scrollregion"] == (0, 0, 330, 680)

    area.canvas.height = 700
    _recalculate(area)

    assert area._scrollbar_visible is False
    assert area.scrollbar.remove_calls == 1
    assert area.canvas.options["scrollregion"] == (0, 0, 330, 700)
