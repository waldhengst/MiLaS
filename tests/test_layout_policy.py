from milas.app import (
    StableLayoutMetrics,
    SIDEBAR_WIDTH,
    WIDE_CANVAS_SIZE,
    choose_sidebar_font_family,
    usable_visualization_width,
    uses_stacked_layout,
)


def test_view_layout_switches_at_the_visualization_width_in_both_directions():
    breakpoint = WIDE_CANVAS_SIZE[0]

    # Narrowing and widening across the same width choose both views in turn.
    states = [
        uses_stacked_layout(width)
        for width in (breakpoint - 1, breakpoint + 1, breakpoint - 1)
    ]
    assert states == [True, False, True]
    # A visible stacked-view scrollbar should not prevent switching back to 2×2.
    assert uses_stacked_layout(breakpoint - 14, scrollbar_width=15) is False


def test_transient_window_activation_measurement_cannot_change_layout():
    stable = StableLayoutMetrics(required_samples=3)
    maximized = (1920, 1080, WIDE_CANVAS_SIZE[0] + 20, 1023, 1.0, "zoomed")
    transient = (1920, 1080, WIDE_CANVAS_SIZE[0] - 80, 1023, 1.0, "zoomed")

    assert stable.observe(maximized) is False
    assert stable.observe(maximized) is False
    # A one-off stale width while focus returns is not considered settled.
    assert stable.observe(transient) is False
    assert stable.observe(maximized) is False
    assert stable.observe(maximized) is False
    assert stable.observe(maximized) is True
    assert transient[2] < WIDE_CANVAS_SIZE[0]
    # The maximized top-level client width still provides enough host width,
    # even if Tk's child canvas reported a stale narrow width during activation.
    assert uses_stacked_layout(usable_visualization_width(maximized[0])) is False


def test_genuine_narrow_resize_is_accepted_after_stable_measurements():
    stable = StableLayoutMetrics(required_samples=3)
    narrow = (1100, 850, 760, 700, 1.25, "normal")

    assert stable.observe(narrow) is False
    assert stable.observe(narrow) is False
    assert stable.observe(narrow) is True
    assert uses_stacked_layout(usable_visualization_width(narrow[0])) is True


def test_visualization_width_uses_client_area_and_existing_sidebar_spacing():
    breakpoint = WIDE_CANVAS_SIZE[0]
    window_chrome_inside_client = 24 + SIDEBAR_WIDTH + 12

    assert usable_visualization_width(breakpoint + window_chrome_inside_client) == breakpoint
    assert uses_stacked_layout(
        usable_visualization_width(breakpoint + window_chrome_inside_client - 1)
    ) is True


def test_new_configure_sequence_resets_transient_measurement_history():
    stable = StableLayoutMetrics(required_samples=3)
    narrow = (1100, 850, 760, 700, 1.25, "normal")
    assert stable.observe(narrow) is False
    assert stable.observe(narrow) is False

    stable.reset()

    assert stable.observe(narrow) is False


def test_sidebar_font_prefers_matplotlib_sans_and_skips_bitmap_families():
    assert choose_sidebar_font_family(
        ["TkFixedFont", "DejaVu Sans Mono", "DejaVu Sans"],
        "DejaVu Sans", "TkFixedFont",
    ) == "DejaVu Sans"
    assert choose_sidebar_font_family(
        ["Fixed", "Arial"], "Unavailable Sans", "Fixed",
    ) == "Arial"


