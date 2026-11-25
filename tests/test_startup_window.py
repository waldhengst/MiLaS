from milas.app import startup_window_height


def test_startup_window_uses_taller_preferred_height_when_it_fits():
    assert startup_window_height(1080) == 960


def test_startup_window_leaves_room_for_desktop_chrome_on_shorter_screens():
    assert startup_window_height(768) == 648
    assert startup_window_height(768) + 120 <= 768


def test_startup_window_height_is_capped_when_screen_is_too_short():
    assert startup_window_height(620) == 500
