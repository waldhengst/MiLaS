"""MiLaS: Milanković Lab for Solar Insolation, an interactive exploration of Earth's orbit, seasons, and solar insolation."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib import colormaps, font_manager, rcParams
from matplotlib import image as mpl_image
from matplotlib.cm import ScalarMappable
from matplotlib.colors import TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.legend_handler import HandlerBase, HandlerTuple
from matplotlib.path import Path as MplPath
from matplotlib.patches import Circle, Polygon
from matplotlib.patheffects import Normal, Stroke
from matplotlib.transforms import blended_transform_factory
from mpl_toolkits.mplot3d import proj3d

from .model import (
    simulate, orbit_xy, true_anomaly_on_date, season_orientation,
    quick_compare_values,
    TODAY_ECCENTRICITY, TODAY_OBLIQUITY_DEG, TODAY_WINTER_SOLSTICE_DEG,
)


# Pixel dimensions measured from the 1920×1080 reference layout.
WIDE_CANVAS_SIZE = (1574, 1023)
PANEL_WIDTHS = (833, 741)
PANEL_HEIGHTS = (629, 394)
STACKED_PANEL_GAP = 30
STACKED_CANVAS_SIZE = (
    max(PANEL_WIDTHS),
    2 * PANEL_HEIGHTS[0] + 2 * PANEL_HEIGHTS[1]
    + 3 * STACKED_PANEL_GAP,
)
SIDEBAR_WIDTH = 306
APP_SHORT_NAME = "MiLaS"
APP_FULL_NAME = "Milanković Lab for Solar Insolation"
APP_SUBTITLE = (
    "The Milanković Lab: Explore Earth’s orbit, seasons, and solar insolation interactively."
)
SIDEBAR_BG = "#202020"
TEXT_DARK = "#3b4147"
TEXT_LIGHT = "#969ca2"
BUTTON_BG = "#3b3f42"
BUTTON_HOVER_BG = "#515a61"
BUTTON_PRESSED_BG = "#3b6070"
BUTTON_BORDER = "#9d9fa0"
BUTTON_HOVER_BORDER = "#a8acb0"
BUTTON_FOCUS = "#4c78a8"
BUTTON_STATE_COLORS = {
    "normal": (BUTTON_BG, "#f2f4f6"),
    "hover": (BUTTON_HOVER_BG, "#ffffff"),
    "pressed": (BUTTON_PRESSED_BG, "#ffffff"),
    "disabled": (BUTTON_BG, "#c7cbd0"),
}


def uses_stacked_layout(available_width, scrollbar_width=0):
    """Return whether the visualization viewport is too narrow for the 2×2 grid."""
    return available_width + scrollbar_width < WIDE_CANVAS_SIZE[0]


def usable_visualization_width(client_width):
    """Derive the visualization host width from the top-level client width.

    Tk child-widget geometry can lag a top-level Configure/activation event.
    The sidebar and shell spacing are fixed, so the window client width gives
    the settled width available to the visualization scroll container.
    """
    shell_padding = 12 * 2
    visualization_gap = 12
    return max(
        0,
        client_width - shell_padding - SIDEBAR_WIDTH - visualization_gap,
    )


class StableLayoutMetrics:
    """Require repeated identical window measurements before changing layout."""

    def __init__(self, required_samples=3):
        self.required_samples = required_samples
        self._candidate = None
        self._sample_count = 0

    def reset(self):
        self._candidate = None
        self._sample_count = 0

    def observe(self, metrics):
        metrics = tuple(metrics)
        if metrics == self._candidate:
            self._sample_count += 1
        else:
            self._candidate = metrics
            self._sample_count = 1
        return self._sample_count >= self.required_samples


def startup_window_height(screen_height, preferred=960, reserved=120):
    """Pick a taller startup height while leaving room for desktop chrome."""
    return max(1, min(preferred, screen_height - reserved))


_NON_SCALABLE_FONT_HINTS = (
    "bitmap", "fixed", "mono", "pixel", "terminal", "courier", "console",
)


def choose_sidebar_font_family(available_families, matplotlib_family, default_family):
    """Prefer Matplotlib's sans font, then a known scalable system sans face."""
    available = {str(name).casefold(): str(name) for name in available_families}
    candidates = (
        matplotlib_family,
        "DejaVu Sans",
        "Segoe UI",
        "Arial",
        "Liberation Sans",
        "Noto Sans",
        "Ubuntu",
        "Cantarell",
        "Helvetica Neue",
        "Helvetica",
        default_family,
    )
    for candidate in candidates:
        if not candidate:
            continue
        family = available.get(str(candidate).casefold())
        if family and not any(hint in family.casefold() for hint in _NON_SCALABLE_FONT_HINTS):
            return family
    for family in available_families:
        name = str(family)
        if not any(hint in name.casefold() for hint in _NON_SCALABLE_FONT_HINTS):
            return name
    return str(default_family)


class RepeatingTimer:
    """Small Tk scheduler matching the start/stop interface used by the app."""

    def __init__(self, root, interval_ms, callback):
        self.root = root
        self.interval_ms = interval_ms
        self.callback = callback
        self._after_id = None
        self._active = False

    def _run(self):
        if not self._active:
            self._after_id = None
            return
        self._after_id = None
        self.callback()
        if self._active:
            try:
                self._after_id = self.root.after(self.interval_ms, self._run)
            except tk.TclError:
                self._after_id = None
                self._active = False

    def start(self):
        if not self._active:
            self._active = True
            self._after_id = self.root.after(self.interval_ms, self._run)

    def stop(self):
        self._active = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def isActive(self):
        return self._active


class VerticalScrollArea(tk.Frame):
    """Canvas-backed vertical scroll area with an auto-hidden scrollbar."""

    def __init__(self, master, *, background, width=None):
        super().__init__(master, background=background, width=width or 1)
        if width:
            # The scroll area contains grid-managed children; lock its width
            # through the matching geometry manager during parent resizes.
            self.grid_propagate(False)
        self.canvas = tk.Canvas(
            self, background=background, borderwidth=0, highlightthickness=0,
        )
        self.scrollbar = tk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview,
            background="#44484b", troughcolor="#252729",
            activebackground="#69737a", highlightthickness=0,
            borderwidth=0, relief="flat",
        )
        self.canvas.configure(yscrollcommand=self._scrollbar_changed)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner = tk.Frame(self.canvas, background=background)
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw",
        )
        self._scroll_region_after_id = None
        self._scrollbar_visible = False
        self.inner.bind(
            "<Configure>", self._schedule_scroll_region_update, add="+",
        )
        self.canvas.bind("<Configure>", self._resize_inner, add="+")

    def _scrollbar_changed(self, first, last):
        # Keep the thumb synchronized, but decide visibility from settled pixel
        # geometry below. Fractions can be stale while Tk processes a resize.
        self.scrollbar.set(first, last)

    def _resize_inner(self, _event=None):
        width = max(1, self.canvas.winfo_width())
        # Height 0 restores the Canvas window to the child's requested height.
        # This prevents a maximized viewport height from becoming stale content
        # height when the window is restored.
        self.canvas.itemconfigure(self.window_id, width=width, height=0)
        self._schedule_scroll_region_update()

    def _schedule_scroll_region_update(self, _event=None):
        if self._scroll_region_after_id is None:
            self._scroll_region_after_id = self.after_idle(
                self._update_scroll_region,
            )

    def _update_scroll_region(self, _event=None):
        self._scroll_region_after_id = None
        try:
            # Let Tk finish the current resize and width-dependent wrapping
            # before comparing the natural content height with the viewport.
            self.update_idletasks()
            width = max(1, self.canvas.winfo_width())
            viewport_height = max(1, self.canvas.winfo_height())
            self.canvas.itemconfigure(self.window_id, width=width, height=0)
            self.update_idletasks()

            content_height = max(1, self.inner.winfo_reqheight())
            content_width = max(width, self.inner.winfo_reqwidth())
            region_height = max(content_height, viewport_height)
            self.canvas.configure(
                scrollregion=(0, 0, content_width, region_height),
            )

            needs_scrollbar = content_height > viewport_height
            if needs_scrollbar and not self._scrollbar_visible:
                self.scrollbar.grid(row=0, column=1, sticky="ns")
                self._scrollbar_visible = True
            elif not needs_scrollbar and self._scrollbar_visible:
                self.scrollbar.grid_remove()
                self._scrollbar_visible = False
        except tk.TclError:
            # A pending idle callback can outlive this widget during shutdown.
            self._scroll_region_after_id = None

    def scroll(self, units):
        self.canvas.yview_scroll(units, "units")


class ParameterSlider(tk.Canvas):
    """A compact, theme-independent slider with an active track and round thumb."""

    def __init__(self, master, variable, minimum, maximum, *, background):
        super().__init__(
            master, height=26, background=background, borderwidth=0,
            highlightthickness=0, takefocus=True, cursor="hand2",
        )
        self._scale_variable = variable
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self._dragging = False
        self._focused = False
        self._hovered = False
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<ButtonPress-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<FocusIn>", self._focus_in, add="+")
        self.bind("<FocusOut>", self._focus_out, add="+")
        self.bind("<KeyPress>", self._key_change, add="+")
        self._scale_variable.trace_add("write", self._redraw)

    def get(self):
        return int(round(float(self._scale_variable.get())))

    value = get

    def set(self, value):
        self._scale_variable.set(
            min(self._maximum, max(self._minimum, int(round(float(value))))),
        )

    setValue = set

    def _track_bounds(self):
        width = max(1, self.winfo_width())
        return 9.0, max(10.0, width - 9.0)

    def _value_x(self):
        left, right = self._track_bounds()
        span = max(1, self._maximum - self._minimum)
        fraction = (self.get() - self._minimum) / span
        return left + fraction * (right - left)

    def _redraw(self, *_args):
        if not self.winfo_exists():
            return
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        left, right = self._track_bounds()
        center_y = height / 2
        thumb_x = self._value_x()
        active = "#347eae" if self._hovered or self._focused else "#2d8f9d"
        self.delete("all")
        self.create_line(
            left, center_y, right, center_y, fill="#c4c9cd", width=4,
            capstyle="round",
        )
        self.create_line(
            left, center_y, thumb_x, center_y, fill=active, width=4,
            capstyle="round",
        )
        self.create_oval(
            thumb_x - 6.5, center_y - 6.5, thumb_x + 6.5, center_y + 6.5,
            fill="#50585d" if not self._hovered else "#39434a",
            outline="#f7f8f9", width=1.2,
        )

    def _set_from_x(self, x):
        left, right = self._track_bounds()
        fraction = min(1.0, max(0.0, (x - left) / max(1.0, right - left)))
        self.set(self._minimum + fraction * (self._maximum - self._minimum))

    def _press(self, event):
        self._dragging = True
        self.focus_set()
        self._set_from_x(event.x)
        return "break"

    def _drag(self, event):
        if self._dragging:
            self._set_from_x(event.x)
        return "break"

    def _release(self, event):
        was_dragging = self._dragging
        self._dragging = False
        if was_dragging:
            self._set_from_x(event.x)
            self._redraw()
            self.event_generate("<<ParameterSliderReleased>>", when="tail")
        return "break"

    def _key_change(self, event):
        increments = {
            "Left": -1, "Down": -1, "Right": 1, "Up": 1,
            "Prior": -10, "Page_Up": 10, "Next": 10, "Page_Down": -10,
        }
        if event.keysym in increments:
            self.set(self.get() + increments[event.keysym])
        elif event.keysym == "Home":
            self.set(self._minimum)
        elif event.keysym == "End":
            self.set(self._maximum)
        else:
            return None
        return "break"

    def _enter(self, _event):
        self._hovered = True
        self._redraw()

    def _leave(self, _event):
        self._hovered = False
        self._redraw()

    def _focus_in(self, _event):
        self._focused = True
        self._redraw()

    def _focus_out(self, _event):
        self._focused = False
        self._redraw()


class HandlerLineWithCenteredMarker(HandlerBase):
    """Draw a full-length line sample with its marker centered over it."""

    def create_artists(
        self, legend, orig_handle, xdescent, ydescent,
        width, height, fontsize, trans,
    ):
        line_handle, marker_handle = orig_handle
        x_start = -xdescent
        y_center = (height - ydescent) / 2
        line_width = 1.8 * fontsize
        marker_size = marker_handle.get_markersize() * legend.markerscale

        line = Line2D(
            [x_start, x_start + line_width], [y_center, y_center],
        )
        self.update_prop(line, line_handle, legend)
        line.set_transform(trans)

        marker = Line2D(
            [x_start + line_width / 2], [y_center],
        )
        self.update_prop(marker, marker_handle, legend)
        marker.set_markersize(marker_size)
        marker.set_transform(trans)
        return [line, marker]


def globe_screen_basis(camera):
    """Keep ecliptic north at the top while showing Earth's axial tilt."""
    camera = np.asarray(camera, dtype=float)
    north = np.array([0.0, 0.0, 1.0])
    up = north - np.dot(north, camera) * camera
    up /= np.linalg.norm(up)
    right = np.cross(up, camera)
    right /= np.linalg.norm(right)
    return right, up


def earth_map_basis(spin_axis):
    """Choose a stable longitude frame for comparing the same regions."""
    spin_axis = np.asarray(spin_axis, dtype=float)
    reference = np.array([1.0, 0.0, 0.0])
    prime_meridian = reference - np.dot(reference, spin_axis) * spin_axis
    if np.linalg.norm(prime_meridian) < 1e-8:
        reference = np.array([0.0, 1.0, 0.0])
        prime_meridian = reference - np.dot(reference, spin_axis) * spin_axis
    prime_meridian /= np.linalg.norm(prime_meridian)
    east = np.cross(spin_axis, prime_meridian)
    return prime_meridian, east


def draw_pole_extensions_2d(ax, direction):
    """Draw pole stubs at their projected surface intersections."""
    for artist in getattr(ax, "_pole_axis_artists", []):
        artist.remove()
    if getattr(ax, "_earth_outline", None) is None:
        ax._earth_outline = Circle(
            (0, 0), 1.0, fill=False, edgecolor="#263746", linewidth=0.7,
            alpha=0.8, zorder=3,
        )
        ax._earth_outline.set_animated(True)
        ax.add_patch(ax._earth_outline)

    direction = np.asarray(direction, dtype=float)
    direction /= np.linalg.norm(direction)
    camera = ax._rotation_state["camera"]
    right, up = globe_screen_basis(camera)

    projected_length = np.linalg.norm(direction - np.dot(direction, camera) * camera)
    near_sign = 1.0 if np.dot(direction, camera) >= 0 else -1.0
    segments = [(near_sign, 1.0, 1.36)]
    if projected_length >= 0.12:
        far_start = 1.0 / projected_length + 0.02
        segments.append((-near_sign, far_start, far_start + 0.36))

    artists = []
    for sign, start_radius, end_radius in segments:
        start = sign * start_radius * direction
        end = sign * end_radius * direction
        line, = ax.plot(
            [np.dot(start, right), np.dot(end, right)],
            [np.dot(start, up), np.dot(end, up)],
            color="#4c78a8", linewidth=2, zorder=4,
        )
        line.set_path_effects([
            Stroke(linewidth=3.8, foreground="white", alpha=0.9), Normal()
        ])
        line.set_animated(True)
        artists.append(line)
        if sign > 0:
            projected_axis = np.array([
                np.dot(direction, right), np.dot(direction, up)
            ])
            projected_length = np.linalg.norm(projected_axis)
            if projected_length > 0.12:
                projected_direction = projected_axis / projected_length
                label_position = (
                    projected_axis + 0.08 * projected_direction
                    + 0.055 * np.array([-projected_direction[1], projected_direction[0]])
                )
                pole_label = ax.text(
                    *label_position, "N",
                    color="#245d8f", fontsize=10, fontweight="bold",
                    ha="center", va="center", zorder=5,
                )
                pole_label.set_path_effects([
                    Stroke(linewidth=2.5, foreground="white", alpha=0.95), Normal()
                ])
                pole_label.set_animated(True)
                artists.append(pole_label)
    ax._pole_axis_artists = artists


def render_globe_image(state, rotation_rad, size=160):
    """Rasterize an orthographic insolation-difference map for animation."""
    camera = state["camera"]
    axis = state["spin_axis"]
    right, up = globe_screen_basis(camera)

    prime_meridian = state["prime_meridian"]
    east = state["east"]
    cosine, sine = np.cos(rotation_rad), np.sin(rotation_rad)
    prime = cosine * prime_meridian + sine * east
    east_rotated = -sine * prime_meridian + cosine * east

    coords = np.linspace(-1.0, 1.0, size, dtype=float)
    screen_x, screen_y = np.meshgrid(coords, coords)
    radius_sq = screen_x**2 + screen_y**2
    inside = radius_sq <= 1.0
    depth = np.sqrt(np.maximum(1.0 - radius_sq, 0.0))
    points = (screen_x[..., None] * right + screen_y[..., None] * up
              + depth[..., None] * camera)
    latitude = np.arcsin(np.clip(points @ axis, -1.0, 1.0))
    longitude = np.arctan2(points @ east_rotated, points @ prime)

    cosine_zenith = (
        np.cos(latitude) * np.cos(state["declination"])
        * np.cos(longitude + rotation_rad)
        + np.sin(latitude) * np.sin(state["declination"])
    )
    selected_insolation = (1361.0 / state["radius_au"]**2) * np.maximum(
        cosine_zenith, 0.0
    )
    reference_cosine_zenith = (
        np.cos(latitude) * np.cos(state["reference_declination"])
        * np.cos(longitude + rotation_rad)
        + np.sin(latitude) * np.sin(state["reference_declination"])
    )
    reference_insolation = (
        1361.0 / state["reference_radius_au"]**2
        * np.maximum(reference_cosine_zenith, 0.0)
    )
    insolation_change = selected_insolation - reference_insolation

    texture = state["texture"]
    image_height, image_width = texture.shape[:2]
    texture_row = np.clip((0.5 - latitude / np.pi) * (image_height - 1),
                          0, image_height - 1)
    texture_col = (np.mod(longitude / (2 * np.pi) + 0.5, 1.0)
                   * (image_width - 1))
    row0 = np.floor(texture_row).astype(int)
    col0 = np.floor(texture_col).astype(int)
    row1 = np.minimum(row0 + 1, image_height - 1)
    col1 = (col0 + 1) % image_width
    row_fraction = (texture_row - row0)[..., None]
    col_fraction = (texture_col - col0)[..., None]
    top = texture[row0, col0] * (1 - col_fraction) + texture[row0, col1] * col_fraction
    bottom = texture[row1, col0] * (1 - col_fraction) + texture[row1, col1] * col_fraction
    surface = top * (1 - row_fraction) + bottom * row_fraction

    heat = state["cmap"](state["norm"](insolation_change))[..., :3]
    daylight = np.clip(cosine_zenith, 0.0, 1.0)
    surface *= (0.78 + 0.22 * np.sqrt(daylight))[..., None]
    # Keep the difference colors prominent while revealing the Earth texture.
    rgb = np.clip(0.62 * heat + 0.38 * surface, 0.0, 1.0)

    latitude_deg = np.rad2deg(latitude)
    longitude_deg = np.rad2deg(longitude)
    latitude_grid = np.abs((latitude_deg + 10.0) % 20.0 - 10.0) < 0.8
    longitude_grid = np.abs((longitude_deg + 15.0) % 30.0 - 15.0) < 0.9
    rgb[latitude_grid] = 0.78 * rgb[latitude_grid] + 0.22
    rgb[longitude_grid] = 0.84 * rgb[longitude_grid] + 0.16

    rgba = np.ones((size, size, 4), dtype=float)
    rgba[..., :3] = rgb
    rgba[..., 3] = inside
    return rgba


def draw_insolation_globe(ax, latitudes_deg, longitudes_deg,
                           season, declination_deg, obliquity_deg, radius_au,
                           reference_declination_deg, reference_radius_au,
                           spin_axis, texture, cmap, norm, rotation_rad=0.0):
    """Render a fixed-view Earth with insolation change relative to today."""
    declination = np.deg2rad(declination_deg)
    reference_declination = np.deg2rad(reference_declination_deg)
    spin_axis = np.asarray(spin_axis, dtype=float)
    prime_meridian, east = earth_map_basis(spin_axis)
    view_latitude = np.deg2rad(40.0)
    camera = (np.cos(view_latitude) * prime_meridian
              + np.sin(view_latitude) * spin_axis)
    camera /= np.linalg.norm(camera)
    state = {
        "latitudes": latitudes_deg,
        "longitudes": longitudes_deg,
        "declination": declination,
        "reference_declination": reference_declination,
        "radius_au": radius_au,
        "reference_radius_au": reference_radius_au,
        "spin_axis": spin_axis,
        "prime_meridian": prime_meridian,
        "east": east,
        "texture": texture,
        "cmap": cmap,
        "norm": norm,
        "camera": camera,
    }
    ax._rotation_state = state
    ax._surface_artist = ax.imshow(
        render_globe_image(state, rotation_rad), extent=(-1.0, 1.0, -1.0, 1.0),
        origin="lower", interpolation="bilinear", zorder=1,
    )
    ax._surface_artist.set_animated(True)
    ax.set(xlim=(-1.4, 1.4), ylim=(-1.4, 1.4))
    ax.set_aspect("equal")
    ax.set_axis_off()
    draw_pole_extensions_2d(ax, spin_axis)
    ax.set_title(f"NH* {season}", fontsize=11, pad=1)
    ax._rotation_phase = rotation_rad
    ax._camera_angles = (
        np.rad2deg(np.arctan2(camera[1], camera[0])),
        np.rad2deg(np.arcsin(np.clip(camera[2], -1.0, 1.0))),
    )


class MainWindow:
    def __init__(self, root):
        self.root = root
        root.title("MiLaS — Milanković Lab for Solar Insolation")
        root.configure(background=SIDEBAR_BG)
        platform_font = tkfont.nametofont("TkDefaultFont")
        # Keep the application font stable even if a user matplotlibrc or
        # system style changes Matplotlib's configured default family.
        matplotlib_font = font_manager.FontProperties(
            family="DejaVu Sans",
        ).get_name()
        sidebar_family = choose_sidebar_font_family(
            tkfont.families(root), matplotlib_font,
            platform_font.actual("family"),
        )
        self._sidebar_font_family = sidebar_family
        self._body_font = tkfont.Font(
            root, family=sidebar_family, size=10, weight="normal",
        )
        self._compact_font = tkfont.Font(
            root, family=sidebar_family, size=9, weight="bold",
        )
        self._parameter_font = tkfont.Font(
            root, family=sidebar_family, size=10, weight="bold",
        )
        self._button_font = tkfont.Font(
            root, family=sidebar_family, size=10, weight="bold",
        )
        self._sidebar_title_font = tkfont.Font(
            root, family=sidebar_family, size=16, weight="bold",
        )
        self._section_heading_font = tkfont.Font(
            root, family=sidebar_family, size=13, weight="bold",
        )
        self._note_heading_font = tkfont.Font(
            root, family=sidebar_family, size=12, weight="bold",
        )
        root.option_add("*Font", self._body_font)
        self._ttk_style = ttk.Style(root)
        for style_name in (
            ".", "TLabel", "TButton", "TScale", "TCheckbutton",
            "TRadiobutton", "TEntry", "TCombobox", "Treeview",
        ):
            self._ttk_style.configure(style_name, font=self._body_font)
        self._ttk_style.configure("Treeview.Heading", font=self._parameter_font)
        self._ttk_style.configure("TNotebook.Tab", font=self._button_font)
        self._ttk_style.configure("MiLaS.TButton", font=self._button_font)
        root.minsize(900, 600)
        rcParams.update({
            "font.family": [sidebar_family],
            "text.color": TEXT_DARK,
            "axes.labelcolor": TEXT_DARK,
            "axes.titlecolor": TEXT_DARK,
            "figure.facecolor": "#ffffff",
            "figure.edgecolor": "#ffffff",
            "axes.facecolor": "#ffffff",
            "axes.edgecolor": "#000000",
            "axes.linewidth": 0.8,
            "grid.color": "#b0b0b0",
            "grid.linestyle": "-",
            "grid.linewidth": 0.8,
            "legend.frameon": True,
            "legend.fancybox": True,
            "legend.framealpha": 0.8,
            "legend.facecolor": "#ffffff",
            "legend.edgecolor": "#cccccc",
            "lines.antialiased": True,
            "xtick.color": TEXT_DARK,
            "ytick.color": TEXT_DARK,
            "legend.labelcolor": TEXT_DARK,
        })

        shell = tk.Frame(root, background=SIDEBAR_BG)
        shell.pack(fill="both", expand=True, padx=12, pady=12)
        self._shell = shell
        self._single_column_layout = True
        self._canvas_resizing = False
        self._responsive_after_id = None
        self._responsive_metric_stabilizer = StableLayoutMetrics()
        self._pending_plot_after_id = None
        self._slider_update_suppression = 0
        self._space_pressed = False

        self.sidebar_scroll = VerticalScrollArea(
            shell, background=SIDEBAR_BG, width=SIDEBAR_WIDTH,
        )
        self.sidebar_scroll.pack(side="left", fill="y")
        panel = self.sidebar_scroll.inner
        self.sidebar_panel = panel
        panel.configure(padx=8)

        title = tk.Label(
            panel, text="Milanković Cycles", anchor="w",
            bg=SIDEBAR_BG, fg="#f2f4f6",
            font=self._sidebar_title_font,
        )
        title.pack(fill="x", pady=(0, 4))

        periods_box = tk.Frame(
            panel, background="#f4f6f8", highlightbackground="#d8dde2",
            highlightthickness=1, bd=0,
        )
        periods_box.pack(fill="x", pady=(0, 4))
        periods_inner = tk.Frame(periods_box, background="#f4f6f8")
        periods_inner.pack(fill="x", padx=8, pady=6)
        for cycle, period in [
            ("Eccentricity", "100,000 years"),
            ("Obliquity", "41,000 years"),
            ("Precession", "19,000–23,000 years"),
        ]:
            row = tk.Frame(periods_inner, background="#f4f6f8")
            row.pack(fill="x", pady=1)
            tk.Label(
                row, text=cycle, anchor="w", bg="#f4f6f8", fg=TEXT_DARK,
                font=self._compact_font,
            ).pack(side="left")
            tk.Label(
                row, text=period, anchor="e", justify="right",
                bg="#f4f6f8", fg=TEXT_DARK, font=self._compact_font,
            ).pack(side="right", fill="x", expand=True)

        reference_panel = self._parameter_panel(
            panel, "Reference Parameters", "#f5f6f7", "#d2d7dc", "#9aa3ab",
            "#68727b",
        )
        self.reference_e_slider, self.reference_e_value = self._slider(
            reference_panel, "Eccentricity", 10, 679,
            round(TODAY_ECCENTRICITY * 10000), "{:.4f}", 10000,
        )
        self.reference_tilt_slider, self.reference_tilt_value = self._slider(
            reference_panel, "Obliquity", 2210, 2450,
            round(TODAY_OBLIQUITY_DEG * 100), "{:.2f}°", 100,
        )
        self.reference_phase_slider, self.reference_phase_value = self._slider(
            reference_panel, "Precession", 0, 359,
            round(TODAY_WINTER_SOLSTICE_DEG), "{:.0f}°", 1,
        )
        self._button(
            reference_panel, "Today", self.reset_reference_to_today,
        ).pack(fill="x", pady=(5, 0))

        comparison_panel = self._parameter_panel(
            panel, "Comparison Parameters", "#f3f7fb", "#cbd8e5", "#7898b8",
            "#58799a",
        )
        self.e_slider, self.e_value = self._slider(
            comparison_panel, "Eccentricity", 10, 679, 167, "{:.4f}", 10000,
        )
        self.tilt_slider, self.tilt_value = self._slider(
            comparison_panel, "Obliquity", 2210, 2450, 2344, "{:.2f}°", 100,
        )
        self.phase_slider, self.phase_value = self._slider(
            comparison_panel, "Precession", 0, 359, 347, "{:.0f}°", 1,
        )
        for name, key in [
            ("Today", "today"),
            ("Minimum Eccentricity", "emin"),
            ("Maximum Eccentricity", "emax"),
            ("Minimum Obliquity", "tiltmin"),
            ("Maximum Obliquity", "tiltmax"),
            ("Opposite Precession Phase", "opposite"),
        ]:
            self._button(
                comparison_panel, name,
                lambda preset=key: self.apply_preset(preset),
            ).pack(fill="x", pady=(4, 0))

        model_note_box = tk.Frame(panel, background=SIDEBAR_BG)
        model_note_box.pack(fill="x", pady=(10, 2))
        self._model_note_heading = tk.Label(
            model_note_box, text="Model note", anchor="w",
            bg=SIDEBAR_BG, fg=TEXT_LIGHT,
            font=self._note_heading_font,
        )
        self._model_note_heading.pack(fill="x")
        self.summary = tk.Label(
            model_note_box, text="", anchor="w", justify="left", wraplength=200,
            bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=self._body_font,
        )
        self.summary.pack(fill="x", pady=(2, 0))
        controls_box = tk.Frame(panel, background=SIDEBAR_BG)
        controls_box.pack(fill="x", pady=(4, 2))
        self._controls_heading = tk.Label(
            controls_box, text="Controls", anchor="w",
            bg=SIDEBAR_BG, fg=TEXT_LIGHT,
            font=self._note_heading_font,
        )
        self._controls_heading.pack(fill="x")
        self._controls_hint = tk.Label(
            controls_box, text="Plots update when a slider is released.",
            anchor="w", justify="left", wraplength=200,
            bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=self._body_font,
        )
        self._controls_hint.pack(fill="x", pady=(2, 0))
        self.rotation_status = tk.Label(
            controls_box, text="Pause/resume Earth rotation with SPACE.",
            anchor="w", justify="left", wraplength=200,
            bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=self._body_font,
        )
        self.rotation_status.pack(fill="x", pady=(2, 0))
        model_note_box.bind(
            "<Configure>", self._update_model_note_wraplength, add="+",
        )
        controls_box.bind(
            "<Configure>", self._update_controls_wraplength, add="+",
        )

        self.visualization_scroll = tk.Frame(shell, background="white")
        self.visualization_scroll.pack(
            side="left", fill="both", expand=True, padx=(12, 0),
        )
        self.visualization_canvas = tk.Canvas(
            self.visualization_scroll, background="white", borderwidth=0,
            highlightthickness=0,
        )
        self.visualization_scrollbar = tk.Scrollbar(
            self.visualization_scroll, orient="vertical",
            command=self.visualization_canvas.yview,
            background="#d4d7da", troughcolor="#f1f2f3",
            activebackground="#aeb5ba", highlightthickness=0,
            borderwidth=0, relief="flat",
        )
        self.visualization_canvas.configure(
            yscrollcommand=self._visualization_scroll_changed,
        )
        self.visualization_canvas.grid(row=0, column=0, sticky="nsew")
        self.visualization_scroll.rowconfigure(0, weight=1)
        self.visualization_scroll.columnconfigure(0, weight=1)

        self.canvas = FigureCanvasTkAgg(
            Figure(
                figsize=(STACKED_CANVAS_SIZE[0] / 100,
                         STACKED_CANVAS_SIZE[1] / 100),
                dpi=100, constrained_layout=True,
            ),
            master=self.visualization_canvas,
        )
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.configure(
            width=STACKED_CANVAS_SIZE[0], height=STACKED_CANVAS_SIZE[1],
            borderwidth=0, highlightthickness=0,
        )
        self._bind_visualization_wheel_events()
        self._canvas_window_id = self.visualization_canvas.create_window(
            (0, 0), window=self.canvas_widget, anchor="n",
        )
        self.visualization_canvas.bind(
            "<Configure>", self._resize_visualization_canvas, add="+",
        )

        self.earth_texture = np.asarray(
            mpl_image.imread(Path(__file__).parent / "assets" / "earth.jpg"),
            dtype=np.float32,
        )
        if self.earth_texture.max() > 1.0:
            self.earth_texture *= 1.0 / 255.0
        self.globe_axes = []
        self._globe_backgrounds = {}
        self._anomaly_tooltip_visible = False
        self._tooltip_window = None
        self._rotation_phase = 0.0
        self._last_rotation_time = time.perf_counter()
        self._calendar_date = date.today()
        self._initial_plot_created = False
        self._rotation_timer = RepeatingTimer(root, 16, self._advance_rotation)
        self._calendar_timer = RepeatingTimer(root, 60_000, self._refresh_calendar_orbit)
        self.canvas.mpl_connect("button_press_event", self._globe_press)
        self.canvas.mpl_connect("motion_notify_event", self._globe_motion)
        self.canvas.mpl_connect("button_release_event", self._globe_release)
        self.canvas.mpl_connect("draw_event", self._cache_globe_backgrounds)
        self.canvas.mpl_connect("motion_notify_event", self._comparison_hover)

        root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        root.bind_all("<Button-4>", self._on_mousewheel, add="+")
        root.bind_all("<Button-5>", self._on_mousewheel, add="+")
        root.bind_all("<KeyPress-space>", self._space_key_down, add="+")
        root.bind_all("<KeyRelease-space>", self._space_key_up, add="+")
        root.bind("<Configure>", self._on_window_configure, add="+")
        root.protocol("WM_DELETE_WINDOW", self._close)

        self._set_stacked_minimum_window_width()
        initial_height = startup_window_height(root.winfo_screenheight())
        root.minsize(self._minimum_window_width, min(600, initial_height))
        root.geometry(f"{self._minimum_window_width}x{initial_height}")
        root.after_idle(self._initial_draw)

    def _button(self, parent, text, command):
        button_height = self._button_font.metrics("linespace") + 4
        border = tk.Frame(
            parent, bg=BUTTON_BORDER, bd=0, highlightthickness=0,
            height=button_height,
        )
        border.pack_propagate(False)
        button = tk.Canvas(
            border, bg=BUTTON_STATE_COLORS["normal"][0], bd=0,
            highlightthickness=0, takefocus=True,
        )
        button.pack(fill="both", expand=True, padx=1, pady=1)
        label = button.create_text(
            0, 0, text=text, font=self._button_font,
            fill=BUTTON_STATE_COLORS["normal"][1], anchor="center",
        )

        def center_label(_event=None):
            button.coords(
                label, button.winfo_width() / 2, button.winfo_height() / 2,
            )

        button.bind("<Configure>", center_label, add="+")
        pointer_focus = [False]
        pointer_inside = [False]
        pointer_pressed = [False]
        keyboard_pressed = [False]
        visual_state = ["normal"]
        keyboard_focus = [False]

        def set_visual_state(state):
            visual_state[0] = state
            background, foreground = BUTTON_STATE_COLORS[state]
            if state == "hover":
                border_color = BUTTON_HOVER_BORDER
            elif keyboard_focus[0]:
                border_color = BUTTON_FOCUS
            else:
                border_color = BUTTON_BORDER
            button.configure(bg=background)
            button.itemconfigure(label, fill=foreground)
            border.configure(bg=border_color)

        def restore_pointer_state():
            set_visual_state("hover" if pointer_inside[0] else "normal")

        def mark_pointer_focus():
            pointer_focus[0] = True
            button.after_idle(lambda: pointer_focus.__setitem__(0, False))

        def on_enter(_event):
            pointer_inside[0] = True
            mark_pointer_focus()
            if not pointer_pressed[0] and not keyboard_pressed[0]:
                set_visual_state("hover")

        def on_leave(_event):
            pointer_inside[0] = False
            if not pointer_pressed[0] and not keyboard_pressed[0]:
                restore_pointer_state()

        def on_press(_event):
            pointer_pressed[0] = True
            mark_pointer_focus()
            button.focus_set()
            button.grab_set()
            set_visual_state("pressed")

        def on_release(event):
            pointer_pressed[0] = False
            try:
                button.grab_release()
            except tk.TclError:
                pass
            inside = (
                0 <= event.x < button.winfo_width()
                and 0 <= event.y < button.winfo_height()
            )
            restore_pointer_state()
            if inside:
                button.after_idle(command)

        def on_key_press(_event):
            keyboard_pressed[0] = True
            set_visual_state("pressed")

        def on_key_release(_event):
            if keyboard_pressed[0]:
                keyboard_pressed[0] = False
                restore_pointer_state()
                button.after_idle(command)

        def on_focus_in(_event):
            keyboard_focus[0] = not pointer_focus[0]
            if not keyboard_focus[0]:
                border_color = (
                    BUTTON_HOVER_BORDER if visual_state[0] == "hover"
                    else BUTTON_BORDER
                )
            else:
                border_color = BUTTON_FOCUS
            border.configure(bg=border_color)

        def on_focus_out(_event):
            keyboard_focus[0] = False
            border_color = (
                BUTTON_HOVER_BORDER if visual_state[0] == "hover"
                else BUTTON_BORDER
            )
            border.configure(bg=border_color)

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")
        button.bind("<ButtonPress-1>", on_press, add="+")
        button.bind("<ButtonRelease-1>", on_release, add="+")
        # SPACE is reserved for the global Earth-rotation shortcut. Binding it
        # here as well would press and activate the focused sidebar button
        # before the toplevel's bind_all handler toggles rotation.
        button.bind("<KeyPress-Return>", on_key_press, add="+")
        button.bind("<KeyRelease-Return>", on_key_release, add="+")
        button.bind("<KeyPress-KP_Enter>", on_key_press, add="+")
        button.bind("<KeyRelease-KP_Enter>", on_key_release, add="+")
        button.bind("<FocusIn>", on_focus_in, add="+")
        button.bind("<FocusOut>", on_focus_out, add="+")
        return border

    def _parameter_panel(self, parent, heading, background, border, accent, heading_color):
        outer = tk.Frame(
            parent, background=background, highlightbackground=border,
            highlightthickness=1, bd=0,
        )
        outer.pack(fill="x", pady=(0, 4))
        tk.Frame(outer, background=accent, width=3).pack(side="left", fill="y")
        content = tk.Frame(outer, background=background)
        content.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        heading_label = tk.Label(
            content, text=heading, anchor="w", justify="left",
            bg=background, fg=heading_color,
            font=self._section_heading_font, wraplength=190,
        )
        heading_label.pack(fill="x", pady=(0, 3))
        content.bind(
            "<Configure>",
            lambda event, label=heading_label: label.configure(
                wraplength=max(1, event.width - 12),
            ),
            add="+",
        )
        return content

    def _slider(self, parent, label, minimum, maximum, value, fmt, scale):
        row = tk.Frame(parent, background=parent.cget("background"))
        row.pack(fill="x", pady=(8, 0))
        tk.Label(
            row, text=label, anchor="w", bg=parent.cget("background"),
            fg=TEXT_DARK, font=self._parameter_font,
        ).pack(side="left", fill="x", expand=True)
        number = tk.Label(
            row, text=fmt.format(value / scale), anchor="e", width=9,
            bg=parent.cget("background"), fg=TEXT_DARK,
            font=self._parameter_font,
        )
        number.pack(side="right")
        variable = tk.IntVar(master=self.root, value=value)
        slider = ParameterSlider(
            parent, variable, minimum, maximum,
            background=parent.cget("background"),
        )
        slider.pack(fill="x", padx=1)
        def update_display(*_args):
            try:
                current = int(variable.get())
            except (tk.TclError, ValueError):
                return
            number.configure(text=fmt.format(current / scale))
            if not slider._dragging and not self._slider_update_suppression:
                self._schedule_plot_update()
        variable.trace_add("write", update_display)
        def release(_event):
            if self._slider_update_suppression:
                return "break"
            self._cancel_scheduled_plot_update()
            self.update_plot()
            return "break"
        slider.bind("<<ParameterSliderReleased>>", release, add="+")
        return slider, number

    def _visualization_scroll_changed(self, first, last):
        self.visualization_scrollbar.set(first, last)
        needs_scrollbar = self._visualization_has_overflow()
        if needs_scrollbar and not self.visualization_scrollbar.winfo_ismapped():
            self.visualization_scrollbar.grid(row=0, column=1, sticky="ns")
            self.visualization_canvas.after_idle(self._resize_visualization_canvas)
        elif not needs_scrollbar and self.visualization_scrollbar.winfo_ismapped():
            self.visualization_scrollbar.grid_remove()
            self.visualization_canvas.after_idle(self._resize_visualization_canvas)

    def _resize_visualization_canvas(self, _event=None):
        if not hasattr(self, "canvas_widget"):
            return
        view_width = max(1, self.visualization_canvas.winfo_width())
        view_height = max(1, self.visualization_canvas.winfo_height())
        size = STACKED_CANVAS_SIZE if self._single_column_layout else WIDE_CANVAS_SIZE
        self.canvas_widget.configure(width=size[0], height=size[1])
        x = max(view_width, size[0]) // 2
        self.visualization_canvas.coords(self._canvas_window_id, x, 0)
        self.visualization_canvas.configure(
            scrollregion=(0, 0, max(view_width, size[0]), size[1]),
        )
        self._visualization_view_height = view_height

    def _visualization_has_overflow(self):
        """Ignore at most one pixel of scroll-region rounding at the viewport edge."""
        region = self.visualization_canvas.cget("scrollregion")
        try:
            x1, y1, x2, y2 = map(float, region.split())
        except (AttributeError, TypeError, ValueError):
            try:
                x1, y1, x2, y2 = map(float, region)
            except (TypeError, ValueError):
                return False
        del x1, x2
        viewport_height = self.visualization_canvas.winfo_height()
        return y2 - y1 > viewport_height + 1

    def _bind_visualization_wheel_events(self):
        """Intercept wheels before Tk, Matplotlib, or global bindings run."""
        sequences = ("<MouseWheel>", "<Button-4>", "<Button-5>")
        widgets = (self.visualization_canvas, self.canvas_widget)
        tag = f"MiLaSVisualizationWheelGuard{id(self)}"
        widgets[0].bind_class(
            tag, sequences[0], self._on_visualization_mousewheel,
        )
        for sequence in sequences[1:]:
            widgets[0].bind_class(
                tag, sequence, self._on_visualization_mousewheel,
            )
        for widget in widgets:
            bindtags = widget.bindtags()
            if tag not in bindtags:
                widget.bindtags((tag, *bindtags))

    def _on_visualization_mousewheel(self, event):
        # This widget-level binding runs before Matplotlib/Tk class bindings.
        # Break immediately when the canvas has no meaningful vertical range.
        if not self._visualization_has_overflow():
            return "break"
        return self._on_mousewheel(event)

    def _on_window_configure(self, event):
        if event.widget is not self.root:
            return
        self._canvas_resizing = True
        self._globe_backgrounds.clear()
        if self._responsive_after_id is not None:
            try:
                self.root.after_cancel(self._responsive_after_id)
            except tk.TclError:
                pass
        # Tk can report child geometry from before a Windows activation/DPI
        # update. Treat Configure as a request to recheck, not as evidence that
        # the visualization host itself became narrower.
        self._responsive_metric_stabilizer.reset()
        self._responsive_after_id = self.root.after(
            140, self._confirm_responsive_layout,
        )

    def _read_responsive_metrics(self):
        scrollbar_mapped = self.visualization_scrollbar.winfo_ismapped()
        return (
            self.root.winfo_width(),
            self.root.winfo_height(),
            self.visualization_canvas.winfo_width(),
            self.visualization_canvas.winfo_height(),
            float(self.root.tk.call("tk", "scaling")),
            str(self.root.state()),
            scrollbar_mapped,
            self.visualization_scrollbar.winfo_width() if scrollbar_mapped else 0,
        )

    def _confirm_responsive_layout(self):
        self._responsive_after_id = None
        metrics = self._read_responsive_metrics()
        if not self._responsive_metric_stabilizer.observe(metrics):
            self._responsive_after_id = self.root.after(
                90, self._confirm_responsive_layout,
            )
            return
        self._apply_responsive_layout(metrics=metrics)

    def _initial_draw(self):
        if self._initial_plot_created:
            return
        self._initial_plot_created = True
        self._apply_responsive_layout(initial=True)
        self.update_plot()
        self._rotation_timer.start()
        self._calendar_timer.start()

    def _apply_responsive_layout(self, initial=False, metrics=None):
        self._responsive_after_id = None
        if metrics is None:
            self.root.update_idletasks()
            metrics = self._read_responsive_metrics()
        # Use the top-level client width minus MiLaS's fixed sidebar and gaps.
        # This equals canvas width plus its optional scrollbar, while avoiding
        # a stale narrow child-canvas measurement during monitor activation.
        available_width = usable_visualization_width(metrics[0])
        narrow = uses_stacked_layout(available_width)
        if not initial and narrow == self._single_column_layout:
            # Activation/configure notifications with unchanged usable width
            # must not resize the figure, rebuild its axes, or trigger a redraw.
            self._canvas_resizing = False
            return
        self._single_column_layout = narrow
        size = STACKED_CANVAS_SIZE if narrow else WIDE_CANVAS_SIZE
        self.canvas.figure.set_size_inches(
            size[0] / self.canvas.figure.dpi,
            size[1] / self.canvas.figure.dpi,
            forward=False,
        )
        self._resize_visualization_canvas()
        if self._initial_plot_created and not initial:
            self.update_plot(request_draw=False)
        self._globe_backgrounds.clear()
        self._canvas_resizing = False
        self.canvas.draw_idle()

    def _set_stacked_minimum_window_width(self):
        """Compute a window width that keeps a stacked panel fully visible."""
        self.root.update_idletasks()
        scrollbar_width = max(15, self.visualization_scrollbar.winfo_reqwidth())
        self._minimum_window_width = max(
            900,
            SIDEBAR_WIDTH + STACKED_CANVAS_SIZE[0] + 24 + 12 + scrollbar_width,
        )
        return self._minimum_window_width

    def _schedule_plot_update(self):
        if self._pending_plot_after_id is None:
            self._pending_plot_after_id = self.root.after_idle(self._run_scheduled_plot_update)

    def _update_model_note_wraplength(self, event):
        wraplength = max(1, event.width - 4)
        self._model_note_heading.configure(wraplength=wraplength)
        self.summary.configure(wraplength=wraplength)

    def _update_controls_wraplength(self, event):
        wraplength = max(1, event.width - 4)
        self._controls_heading.configure(wraplength=wraplength)
        self._controls_hint.configure(wraplength=wraplength)
        self.rotation_status.configure(wraplength=wraplength)

    def _cancel_scheduled_plot_update(self):
        if self._pending_plot_after_id is not None:
            try:
                self.root.after_cancel(self._pending_plot_after_id)
            except tk.TclError:
                pass
            self._pending_plot_after_id = None

    def _run_scheduled_plot_update(self):
        self._pending_plot_after_id = None
        self.update_plot()

    def _toggle_rotation(self):
        if self._rotation_timer.isActive():
            self._rotation_timer.stop()
        else:
            self._last_rotation_time = time.perf_counter()
            self._rotation_timer.start()

    def _space_key_down(self, _event):
        if not self._space_pressed:
            self._space_pressed = True
            self._toggle_rotation()
        return "break"

    def _space_key_up(self, _event):
        self._space_pressed = False
        return "break"

    def _advance_rotation(self):
        now = time.perf_counter()
        elapsed = min(now - self._last_rotation_time, 0.1)
        self._last_rotation_time = now
        self._rotation_phase = (
            self._rotation_phase + np.deg2rad(12.0 * elapsed)
        ) % (2 * np.pi)
        if self._canvas_resizing:
            return
        for ax in self.globe_axes:
            state = ax._rotation_state
            ax._surface_artist.set_data(render_globe_image(state, self._rotation_phase))
        if self._globe_backgrounds:
            self._blit_globes()
        else:
            self.canvas.draw_idle()

    def _refresh_calendar_orbit(self):
        current_date = date.today()
        if current_date != self._calendar_date:
            self._calendar_date = current_date
            self.update_plot()

    def _cache_globe_backgrounds(self, _event):
        if self._canvas_resizing:
            self._globe_backgrounds.clear()
            return
        self._globe_backgrounds = {
            ax: self.canvas.copy_from_bbox(ax.bbox) for ax in self.globe_axes
        }
        if not self._rotation_timer.isActive():
            self._blit_globes()

    def _blit_globes(self):
        if self._canvas_resizing:
            return
        for ax in self.globe_axes:
            background = self._globe_backgrounds.get(ax)
            if background is None:
                continue
            self.canvas.restore_region(background)
            ax.draw_artist(ax._surface_artist)
            if ax._earth_outline is not None:
                ax.draw_artist(ax._earth_outline)
            for artist in ax._pole_axis_artists:
                ax.draw_artist(artist)
        self.canvas.blit()

    def _globe_press(self, event):
        if event.inaxes not in self.globe_axes or event.button != 1:
            return
        event.inaxes._drag_start = (event.x, event.y, *event.inaxes._camera_angles)

    def _globe_motion(self, event):
        ax = event.inaxes
        if ax not in self.globe_axes or getattr(ax, "_drag_start", None) is None:
            return
        start_x, start_y, start_azimuth, start_elevation = ax._drag_start
        azimuth = start_azimuth - (event.x - start_x) * 0.5
        elevation = np.clip(start_elevation + (event.y - start_y) * 0.5, -85, 85)
        azimuth_rad, elevation_rad = np.deg2rad([azimuth, elevation])
        camera = np.array([
            np.cos(elevation_rad) * np.cos(azimuth_rad),
            np.cos(elevation_rad) * np.sin(azimuth_rad),
            np.sin(elevation_rad),
        ])
        for globe_ax in self.globe_axes:
            globe_ax._rotation_state["camera"] = camera.copy()
            globe_ax._camera_angles = (azimuth, elevation)
            globe_ax._surface_artist.set_data(
                render_globe_image(globe_ax._rotation_state, self._rotation_phase)
            )
            draw_pole_extensions_2d(globe_ax, globe_ax._rotation_state["spin_axis"])
        if self._canvas_resizing:
            return
        if self._globe_backgrounds:
            self._blit_globes()
        else:
            self.canvas.draw_idle()

    def _globe_release(self, _event):
        for globe_ax in self.globe_axes:
            globe_ax._drag_start = None

    def _comparison_hover(self, event):
        table = getattr(self, "comparison_table", None)
        if table is None or event.x is None or event.y is None:
            return
        cell = table.get_celld().get((3, 0))
        if cell is None:
            return
        over_anomaly = cell.get_window_extent(
            self.canvas.get_renderer()
        ).contains(event.x, event.y)
        if over_anomaly and not self._anomaly_tooltip_visible:
            self._anomaly_tooltip_visible = True
            self._show_anomaly_tooltip()
        elif not over_anomaly and self._anomaly_tooltip_visible:
            self._anomaly_tooltip_visible = False
            self._hide_tooltip()

    def _show_anomaly_tooltip(self):
        if self._tooltip_window is not None:
            return
        self._tooltip_window = tk.Toplevel(self.root)
        self._tooltip_window.wm_overrideredirect(True)
        self._tooltip_window.configure(background="#fffbe6")
        tk.Label(
            self._tooltip_window,
            text=("Angle from perihelion to Earth's orbital position at the "
                  "Northern Hemisphere winter solstice."),
            background="#fffbe6", foreground=TEXT_DARK,
            font=self._body_font,
            relief="solid", borderwidth=1, padx=5, pady=3,
            wraplength=300, justify="left",
        ).pack()
        x = self.root.winfo_pointerx() + 12
        y = self.root.winfo_pointery() + 12
        self._tooltip_window.geometry(f"+{x}+{y}")

    def _hide_tooltip(self):
        if self._tooltip_window is not None:
            self._tooltip_window.destroy()
            self._tooltip_window = None

    def _on_mousewheel(self, event):
        x, y = event.x_root, event.y_root
        sidebar_bounds = (
            self.sidebar_scroll.winfo_rootx(), self.sidebar_scroll.winfo_rooty(),
            self.sidebar_scroll.winfo_width(), self.sidebar_scroll.winfo_height(),
        )
        visualization_bounds = (
            self.visualization_scroll.winfo_rootx(), self.visualization_scroll.winfo_rooty(),
            self.visualization_scroll.winfo_width(), self.visualization_scroll.winfo_height(),
        )
        def contains(bounds):
            left, top, width, height = bounds
            return left <= x < left + width and top <= y < top + height
        if contains(sidebar_bounds):
            target = self.sidebar_scroll
        elif contains(visualization_bounds):
            if not self._visualization_has_overflow():
                return "break"
            target = self.visualization_canvas
        else:
            return None
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            units = -int(event.delta / 120) * 3
        if units:
            if target is self.sidebar_scroll:
                target.scroll(units)
            else:
                target.yview_scroll(units, "units")
        return "break"

    def _close(self):
        self._rotation_timer.stop()
        self._calendar_timer.stop()
        self.root.destroy()

    @staticmethod
    def _slider_value_changed(slider):
        if not slider._dragging:
            slider.event_generate("<KeyRelease>")

    def reset_reference_to_today(self):
        self._slider_update_suppression += 1
        try:
            self.reference_e_slider.setValue(round(TODAY_ECCENTRICITY * 10000))
            self.reference_tilt_slider.setValue(round(TODAY_OBLIQUITY_DEG * 100))
            self.reference_phase_slider.setValue(round(TODAY_WINTER_SOLSTICE_DEG))
        finally:
            self._slider_update_suppression -= 1
        self.reference_e_value.configure(
            text=f"{self.reference_e_slider.value() / 10000:.4f}"
        )
        self.reference_tilt_value.configure(
            text=f"{self.reference_tilt_slider.value() / 100:.2f}°"
        )
        self.reference_phase_value.configure(
            text=f"{self.reference_phase_slider.value():.0f}°"
        )
        self.update_plot()

    def apply_preset(self, preset):
        e = self.e_slider.value() / 10000
        tilt = self.tilt_slider.value() / 100
        phase = self.phase_slider.value()
        e, tilt, phase = quick_compare_values(preset, e, tilt, phase)
        self._slider_update_suppression += 1
        try:
            self.e_slider.setValue(round(e * 10000))
            self.tilt_slider.setValue(round(tilt * 100))
            self.phase_slider.setValue(round(phase) % 360)
        finally:
            self._slider_update_suppression -= 1
        self.update_plot()

    def _on_mousewheel_compat(self, event):
        return self._on_mousewheel(event)

    def _on_window_close(self):
        self._close()

    def _create_plot_grid(self, fig, single_column):
        """Create fixed-pixel 2×2 or 1×4 panel cells."""
        if single_column:
            grid = fig.add_gridspec(
                7, 1,
                height_ratios=(629, 30, 629, 30, 394, 30, 394),
                hspace=0, left=0, right=1, bottom=0, top=1,
            )
            orbit_cell = grid[2, 0].subgridspec(
                1, 3, width_ratios=(46, 741, 46), wspace=0,
            )[0, 1]
            annual_cell = grid[6, 0].subgridspec(
                1, 3, width_ratios=(46, 741, 46), wspace=0,
            )[0, 1]
            return grid[0, 0], orbit_cell, grid[4, 0], annual_cell

        grid = fig.add_gridspec(
            2, 2,
            height_ratios=PANEL_HEIGHTS,
            width_ratios=PANEL_WIDTHS,
            wspace=0, hspace=0, left=0, right=1, bottom=0, top=1,
        )
        return grid[0, 0], grid[0, 1], grid[1, 0], grid[1, 1]

    @staticmethod
    def _add_panel_titles(fig, cells):
        """Place titles at a shared pixel offset from each panel's top edge."""
        labels = (
            "Insolation Differences", "Earth’s Orbit",
            "Comparison vs Reference", "Annual Mean Insolation",
        )
        panel_title_offset = 24 / fig.bbox.height
        titles = []
        for cell, label in zip(cells, labels):
            bounds = cell.get_position(fig)
            titles.append(fig.text(
                bounds.x0 + bounds.width / 2,
                bounds.y1 - panel_title_offset,
                label,
                ha="center", va="baseline",
                fontsize=14, fontweight="bold", color="#3b4147",
            ))
        return titles

    @staticmethod
    def _add_panel_separators(fig, globe_cell, orbit_cell,
                              comparison_cell, annual_cell):
        """Overlay subtle one-pixel separators on the shared 2×2 grid edges."""
        globe_bounds = globe_cell.get_position(fig)
        orbit_bounds = orbit_cell.get_position(fig)
        comparison_bounds = comparison_cell.get_position(fig)
        annual_bounds = annual_cell.get_position(fig)
        vertical_boundary = (globe_bounds.x1 + orbit_bounds.x0) / 2
        horizontal_boundary = (comparison_bounds.y1 + globe_bounds.y0) / 2
        line_style = {
            "color": "#E3E6E8",
            "linewidth": 72 / fig.dpi,
            "transform": fig.transFigure,
            "solid_capstyle": "butt",
            "antialiased": False,
            "snap": True,
            "clip_on": False,
            "zorder": 1000,
        }
        separators = (
            Line2D([vertical_boundary, vertical_boundary], [0, 1], **line_style),
            Line2D([0, 1], [horizontal_boundary, horizontal_boundary], **line_style),
        )
        for separator in separators:
            fig.add_artist(separator)
        return separators

    @staticmethod
    def _add_stacked_panel_separators(fig, cells):
        """Draw matching dividers in the gaps between stacked panels."""
        bounds = [cell.get_position(fig) for cell in cells]
        line_style = {
            "color": "#E3E6E8",
            "linewidth": 72 / fig.dpi,
            "transform": fig.transFigure,
            "solid_capstyle": "butt",
            "antialiased": False,
            "snap": True,
            "clip_on": False,
            "zorder": 1000,
        }
        separators = tuple(
            Line2D(
                [0, 1],
                [(upper.y0 + lower.y1) / 2] * 2,
                **line_style,
            )
            for upper, lower in zip(bounds, bounds[1:])
        )
        for separator in separators:
            fig.add_artist(separator)
        return separators

    def update_plot(self, *_, request_draw=True):
        current_date = date.today()
        self._calendar_date = current_date
        e = self.e_slider.value() / 10000
        tilt = self.tilt_slider.value() / 100
        phase = self.phase_slider.value()
        reference_e = self.reference_e_slider.value() / 10000
        reference_tilt = self.reference_tilt_slider.value() / 100
        reference_phase = self.reference_phase_slider.value()
        result = simulate(e, tilt, phase, samples=360)
        fig = self.canvas.figure
        self._globe_backgrounds = {}
        fig.clear()
        fig.set_layout_engine("none")
        globe_cell, orbit_cell, comparison_cell, annual_cell = (
            self._create_plot_grid(fig, self._single_column_layout)
        )
        self.panel_titles = self._add_panel_titles(
            fig, (globe_cell, orbit_cell, comparison_cell, annual_cell),
        )

        # Schematic orbital context; the title has its own row so it never
        # overlaps the orbit, annotations, or legend.
        orbit_grid = orbit_cell.subgridspec(
            3, 1,
            height_ratios=(0.07, 0.80, 0.13), hspace=0.0,
        )
        orbit_heading_ax = fig.add_subplot(orbit_grid[0, 0])
        orbit_heading_ax.set_axis_off()
        ax = fig.add_subplot(orbit_grid[1, 0], projection="3d")
        # Keep the orbit plot's explicit artist layers. Axes3D otherwise
        # recomputes collection z-orders from camera depth on every draw,
        # which can put a Reference event marker above its Comparison pair.
        ax.computed_zorder = False
        orbit_legend_ax = fig.add_subplot(orbit_grid[2, 0])
        orbit_legend_ax.set_axis_off()
        anomaly = np.linspace(0, 2 * np.pi, 600)
        ox, oy = orbit_xy(e, anomaly)
        earth_angle = true_anomaly_on_date(e, phase, current_date)
        earth_x, earth_y = orbit_xy(e, np.array([earth_angle]))
        earth_center = (earth_x[0], earth_y[0], 0.0)
        reference_earth_angle = true_anomaly_on_date(
            reference_e, reference_phase, current_date,
        )
        reference_earth_x, reference_earth_y = orbit_xy(
            reference_e, np.array([reference_earth_angle]),
        )
        reference_earth_center = (
            reference_earth_x[0], reference_earth_y[0], 0.0,
        )
        earth_radius = 0.028
        earth_clearance = np.abs(
            (anomaly - earth_angle + np.pi) % (2 * np.pi) - np.pi
        ) > 0.035
        selected_x = np.where(earth_clearance, ox, np.nan)
        selected_y = np.where(earth_clearance, oy, np.nan)
        ax.plot(
            selected_x, selected_y, np.zeros_like(selected_x),
            color="#4c78a8", linewidth=2, label="_nolegend_",
        )
        ref_x, ref_y = orbit_xy(reference_e, anomaly)
        reference_clearance = (
            np.hypot(ref_x - earth_center[0], ref_y - earth_center[1])
            > earth_radius * 1.1
        )
        reference_x = np.where(reference_clearance, ref_x, np.nan)
        reference_y = np.where(reference_clearance, ref_y, np.nan)
        reference_dash_pattern = (0, (3.7, 3.7))
        ax.plot(
            reference_x, reference_y, np.zeros_like(reference_x),
            color="#858585", linewidth=1.8,
            linestyle=reference_dash_pattern,
            label="_nolegend_",
        )
        overlap_line, = ax.plot(
            reference_x, reference_y, np.zeros_like(reference_x),
            color="white", linewidth=1.8,
            linestyle=reference_dash_pattern,
            label="_nolegend_",
        )

        pixels_to_points = 72 / fig.dpi
        sun_size = (32 * pixels_to_points) ** 2
        ax.scatter([0], [0], [0], color="#f2a900", s=sun_size,
                   depthshade=True, zorder=8, label="Sun")
        colors = ["#4c78a8", "#59a14f", "#e15759", "#b07aa1"]
        season_offsets = np.array([0, 90, 180, 270])
        season_angles = np.deg2rad(phase + season_offsets)
        season_names = ["Solstice", "Equinox", "Solstice", "Equinox"]
        season_marker_shapes = ["^", "*", "^", "*"]
        season_points = []
        for angle, color, name, marker_shape in zip(
            season_angles, colors, season_names, season_marker_shapes,
        ):
            sx, sy = orbit_xy(e, np.array([angle]))
            season_points.append(
                (sx[0], sy[0], angle, color, name, marker_shape)
            )

        # Calculate the Comparison perihelion and aphelion positions.
        perihelion_x, perihelion_y = orbit_xy(e, np.array([0.0]))
        aphelion_x, aphelion_y = orbit_xy(e, np.array([np.pi]))
        earth_size = (16 * pixels_to_points) ** 2

        def event_marker_size(marker_shape, circle_size):
            marker_style = MarkerStyle(marker_shape)
            marker_path = marker_style.get_path().transformed(
                marker_style.get_transform(),
            )
            bounds = marker_path.get_extents()
            max_extent = max(bounds.width, bounds.height)
            return circle_size / max_extent ** 2

        markers_coincide = np.array_equal(
            reference_earth_center, earth_center,
        )
        reference_edge = "#858585" if markers_coincide else "white"
        reference_size = (
            (18 * pixels_to_points) ** 2 if markers_coincide else earth_size
        )
        ax.scatter(
            [reference_earth_center[0]],
            [reference_earth_center[1]],
            [reference_earth_center[2]],
            color="#858585", edgecolors=reference_edge, linewidths=0.8,
            s=reference_size,
            depthshade=False, zorder=20, label="_nolegend_",
        )
        ax.scatter(
            [earth_center[0]], [earth_center[1]], [earth_center[2]],
            color="#4c78a8", edgecolors="white", linewidths=0.7,
            s=earth_size, depthshade=False, zorder=21,
            label="_nolegend_",
        )
        # Add Reference event markers first so Comparison markers render above
        # their matching markers when they share a projected position.
        reference_season_angles = np.deg2rad(
            reference_phase + season_offsets
        )
        reference_season_x, reference_season_y = orbit_xy(
            reference_e, reference_season_angles,
        )
        for comparison_event, marker_x, marker_y, marker_shape in zip(
            season_points, reference_season_x, reference_season_y,
            season_marker_shapes,
        ):
            comparison_x, comparison_y = comparison_event[:2]
            markers_coincide = np.array_equal(
                np.array([marker_x, marker_y, 0.018]),
                np.array([comparison_x, comparison_y, 0.018]),
            )
            reference_edge = "#858585" if markers_coincide else "white"
            reference_circle_size = (
                (18 * pixels_to_points) ** 2 if markers_coincide else earth_size
            )
            reference_marker_size = (
                event_marker_size(
                    marker_shape, reference_circle_size,
                )
            )
            ax.scatter(
                [marker_x], [marker_y], [0.018], marker=marker_shape,
                s=reference_marker_size, facecolors="#858585",
                edgecolors=reference_edge, linewidths=0.8,
                depthshade=False, zorder=20,
            )

        reference_perihelion_x, reference_perihelion_y = orbit_xy(
            reference_e, np.array([0.0]),
        )
        reference_aphelion_x, reference_aphelion_y = orbit_xy(
            reference_e, np.array([np.pi]),
        )
        for marker_x, marker_y, shape in (
            (reference_perihelion_x, reference_perihelion_y, "s"),
            (reference_aphelion_x, reference_aphelion_y, "D"),
        ):
            comparison_x, comparison_y = (
                (perihelion_x, perihelion_y)
                if shape == "s"
                else (aphelion_x, aphelion_y)
            )
            markers_coincide = np.array_equal(
                np.array([marker_x[0], marker_y[0], 0.024]),
                np.array([comparison_x[0], comparison_y[0], 0.024]),
            )
            reference_edge = "#858585" if markers_coincide else "white"
            reference_circle_size = (
                (18 * pixels_to_points) ** 2 if markers_coincide else earth_size
            )
            reference_marker_size = (
                event_marker_size(
                    shape, reference_circle_size,
                )
            )
            ax.scatter(
                marker_x, marker_y, [0.024], marker=shape,
                s=reference_marker_size, facecolors="#858585",
                edgecolors=reference_edge, linewidths=0.8,
                depthshade=False, zorder=20,
            )

        # Comparison markers are added after Reference markers, matching the
        # Earth-marker draw order. Their calibrated sizes are reapplied on each
        # plot redraw.
        ax.scatter(
            perihelion_x, perihelion_y, [0.024], marker="s",
            s=event_marker_size("s", earth_size),
            facecolors="#4c78a8", edgecolors="white", linewidths=0.7,
            zorder=21,
        )
        ax.scatter(
            aphelion_x, aphelion_y, [0.024], marker="D",
            s=event_marker_size("D", earth_size),
            facecolors="#4c78a8", edgecolors="white", linewidths=0.7,
            zorder=21,
        )
        for season in season_points:
            sx, sy, angle, color, name, marker_shape = season
            ax.scatter(
                [sx], [sy], [0.018],
                s=event_marker_size(marker_shape, earth_size),
                marker=marker_shape,
                facecolors="#4c78a8", edgecolors="white",
                linewidths=0.7, zorder=21,
            )

        # The maximum slider eccentricity is 0.0679, giving an aphelion of
        # 1.0679 AU. Pan the equal-scaled view slightly right to center the
        # orbit paths and Sun within their panel.
        orbit_limit = 1.22
        orbit_center_x = -0.10
        ax.set_xlim(
            orbit_center_x - orbit_limit,
            orbit_center_x + orbit_limit,
        )
        ax.set_ylim(-orbit_limit, orbit_limit)
        ax.set_zlim(-0.14, 0.14)
        ax.set_box_aspect((1, 1, 0.32), zoom=1.45)
        ax.view_init(elev=65, azim=-58)

        # Keep the direction indicator fixed relative to the Sun, independent
        # of the changing orbital paths and markers. Its radius is halfway
        # back toward the lower orbit from the preceding inset position.
        arrow_center_angle = np.deg2rad(301.1)
        arrow_radius = 0.8673
        arrow_length = 0.28 * np.deg2rad(55)
        half_span = arrow_length / (2 * arrow_radius)
        arrow_angles = np.array([
            arrow_center_angle - half_span,
            arrow_center_angle + half_span,
        ])
        arrow_x = arrow_radius * np.cos(arrow_angles)
        arrow_y = arrow_radius * np.sin(arrow_angles)
        arrow_projected_x, arrow_projected_y, _ = proj3d.proj_transform(
            arrow_x, arrow_y, np.zeros_like(arrow_x), ax.get_proj(),
        )
        arrow_curve_rad = 0.12
        arrow_start_display, arrow_end_display = ax.transData.transform(
            np.column_stack((arrow_projected_x, arrow_projected_y))
        )
        curve_midpoint_display_x = (
            (arrow_start_display[0] + arrow_end_display[0]) / 2
            + 0.5 * arrow_curve_rad
            * (arrow_end_display[1] - arrow_start_display[1])
        )
        curve_midpoint_display_y = (
            arrow_start_display[1] + arrow_end_display[1]
        ) / 2
        sun_projected = proj3d.proj_transform(
            0.0, 0.0, 0.0, ax.get_proj(),
        )
        sun_display = ax.transData.transform(sun_projected[:2])
        curve_midpoint_x = ax.transData.inverted().transform((
            curve_midpoint_display_x, curve_midpoint_display_y,
        ))[0]
        sun_center_x = ax.transData.inverted().transform((
            sun_display[0], curve_midpoint_display_y,
        ))[0]
        arrow_projected_x += sun_center_x - curve_midpoint_x
        ax.annotate(
            "", xy=(arrow_projected_x[1], arrow_projected_y[1]),
            xytext=(arrow_projected_x[0], arrow_projected_y[0]),
            xycoords="data", textcoords="data",
            annotation_clip=False,
            arrowprops={
                "arrowstyle": "->",
                "connectionstyle": f"arc3,rad={arrow_curve_rad}",
                "mutation_scale": 9 * pixels_to_points,
                "linewidth": 1.1,
                "color": "#111111",
                "shrinkA": 0, "shrinkB": 0,
            },
            zorder=30,
        )

        projected_comparison = proj3d.proj_transform(
            selected_x, selected_y, np.zeros_like(selected_x), ax.get_proj(),
        )
        comparison_display = ax.transData.transform(np.column_stack(
            projected_comparison[:2]
        ))
        finite_points = np.isfinite(comparison_display).all(axis=1)
        finite_indices = np.flatnonzero(finite_points)
        split_indices = np.flatnonzero(np.diff(finite_indices) > 1) + 1
        orbit_runs = np.split(finite_indices, split_indices)
        clip_vertices = []
        clip_codes = []
        half_width_pixels = 1.0
        for run in orbit_runs:
            if run.size < 2:
                continue
            points = comparison_display[run]
            tangents = np.gradient(points, axis=0)
            lengths = np.linalg.norm(tangents, axis=1)
            lengths[lengths == 0] = 1.0
            normals = np.column_stack((-tangents[:, 1], tangents[:, 0]))
            normals /= lengths[:, None]
            upper = points + normals * half_width_pixels
            lower = points - normals * half_width_pixels
            display_polygon = np.vstack((upper, lower[::-1], upper[:1]))
            axes_polygon = ax.transAxes.inverted().transform(display_polygon)
            clip_vertices.append(axes_polygon)
            clip_codes.append(np.array(
                [MplPath.MOVETO]
                + [MplPath.LINETO] * (len(axes_polygon) - 2)
                + [MplPath.CLOSEPOLY],
                dtype=np.uint8,
            ))
        if clip_vertices:
            overlap_clip = MplPath(
                np.concatenate(clip_vertices), np.concatenate(clip_codes),
            )
            overlap_line.set_clip_path(overlap_clip, ax.transAxes)
        ax.set_axis_off()
        legend_handles = [
            (
                Line2D([], [], color="#4c78a8", linewidth=2, linestyle="-"),
                Line2D([], [], linestyle="None", marker="o", markersize=6,
                       markerfacecolor="#4c78a8", markeredgecolor="white",
                       markeredgewidth=0.7),
            ),
            (
                Line2D([], [], color="#858585", linewidth=1.8, linestyle="-"),
                Line2D([], [], linestyle="None", marker="o", markersize=6,
                       markerfacecolor="#858585", markeredgecolor="white",
                       markeredgewidth=0.7),
            ),
        ]
        sun_projected_x, _, _ = proj3d.proj_transform(0.0, 0.0, 0.0, ax.get_proj())
        orbit_legend_transform = blended_transform_factory(
            ax.transData, orbit_legend_ax.transAxes,
        )
        orbit_legend = orbit_legend_ax.legend(
            handles=legend_handles, labels=["Comparison", "Reference"],
            loc="center", bbox_to_anchor=(sun_projected_x, 0.63),
            bbox_transform=orbit_legend_transform,
            handler_map={tuple: HandlerLineWithCenteredMarker()},
            fontsize=11, ncol=2, frameon=False, handlelength=1.8,
            labelspacing=0.35,
        )
        orbit_legend_ax.add_artist(orbit_legend)
        event_legend = orbit_legend_ax.legend(
            handles=[
                Line2D(
                    [], [], linestyle="None", marker=shape,
                    markersize=8 if shape == "*" else 7,
                    markerfacecolor="none", markeredgecolor="#3b4147",
                    markeredgewidth=0.9,
                )
                for shape in ("o", "^", "*", "s", "D")
            ],
            labels=["Earth", "Solstice", "Equinox", "Perihelion", "Aphelion"],
            loc="center", bbox_to_anchor=(sun_projected_x, 0.20),
            bbox_transform=orbit_legend_transform,
            fontsize=11, ncol=5, frameon=False, handlelength=0.9,
            handletextpad=0.35, columnspacing=0.9, borderaxespad=0,
        )

        globe_grid = globe_cell.subgridspec(
            3, 2, height_ratios=(0.08, 1.0, 0.16),
            wspace=0.08, hspace=0.015,
        )
        heading_ax = fig.add_subplot(globe_grid[0, :])
        heading_ax.set_axis_off()
        globe_latitudes = np.linspace(-90, 90, 73)
        globe_longitudes = np.linspace(-180, 180, 145)
        globe_cmap = colormaps["RdBu_r"]
        globe_norm = TwoSlopeNorm(vmin=-250, vcenter=0, vmax=250)
        globe_axes = []
        solstice_distances = {}
        seasons = [("Summer Solstice", 180), ("Winter Solstice", 0)]
        for index, (season, offset_deg) in enumerate(seasons):
            # At the selected solstices, declination is exactly ± the tilt.
            solstice_nu = np.deg2rad((phase + offset_deg) % 360)
            radius_au = (1 - e**2) / (1 + e * np.cos(solstice_nu))
            declination, spin_axis, _, _ = season_orientation(
                tilt, phase, solstice_nu
            )
            reference_solstice_nu = np.deg2rad(
                (reference_phase + offset_deg) % 360
            )
            reference_declination, _, _, _ = season_orientation(
                reference_tilt, reference_phase,
                reference_solstice_nu,
            )
            reference_radius_au = (
                1 - reference_e**2
            ) / (
                1 + reference_e * np.cos(reference_solstice_nu)
            )
            solstice_distances[season] = (radius_au, reference_radius_au)
            globe_ax = fig.add_subplot(globe_grid[1, index])
            draw_insolation_globe(
                globe_ax, globe_latitudes, globe_longitudes,
                season, np.rad2deg(declination), tilt, radius_au,
                np.rad2deg(reference_declination), reference_radius_au,
                spin_axis, self.earth_texture,
                globe_cmap, globe_norm, self._rotation_phase,
            )
            globe_axes.append(globe_ax)

        self.globe_axes = globe_axes

        comparison_ax = fig.add_subplot(comparison_cell)
        comparison_ax.set_axis_off()
        angular_difference = (phase - reference_phase + 180) % 360 - 180
        comparison_rows = [
            ("Eccentricity", f"{e:.4f}",
             f"{reference_e:.4f}", f"{e - reference_e:+.4f}"),
            ("Obliquity", f"{tilt:.2f}°",
             f"{reference_tilt:.2f}°", f"{tilt - reference_tilt:+.2f}°"),
            (
                "Precession",
                f"{phase:.1f}°", f"{reference_phase:.1f}°",
                f"{angular_difference:+.1f}°",
            ),
            (
                "Sun distance at NH* summer solstice",
                f"{solstice_distances['Summer Solstice'][0]:.3f} AU",
                f"{solstice_distances['Summer Solstice'][1]:.3f} AU",
                f"{solstice_distances['Summer Solstice'][0] - solstice_distances['Summer Solstice'][1]:+.3f} AU",
            ),
            (
                "Sun distance at NH* winter solstice",
                f"{solstice_distances['Winter Solstice'][0]:.3f} AU",
                f"{solstice_distances['Winter Solstice'][1]:.3f} AU",
                f"{solstice_distances['Winter Solstice'][0] - solstice_distances['Winter Solstice'][1]:+.3f} AU",
            ),
        ]
        self.comparison_table = comparison_ax.table(
            cellText=[list(row) for row in comparison_rows],
            colLabels=(
                ["Quantity", "Comparison", "Reference", "Δ (Comp. − Ref.)"]
            ),
            colWidths=[0.51, 0.15, 0.13, 0.21],
            cellLoc="center",
            bbox=[0.01, 0.15, 0.98, 0.73],
        )
        self.comparison_table.auto_set_font_size(False)
        self.comparison_table.set_fontsize(10)
        data_row_height = max(
            cell.get_height()
            for (row, _column), cell in self.comparison_table.get_celld().items()
            if row > 0
        )
        for (row, column), cell in self.comparison_table.get_celld().items():
            if row > 0:
                cell.set_height(data_row_height)
            cell.set_linewidth(0.25)
            cell.set_edgecolor("#cccccc")
            if row == 0:
                cell.set_text_props(weight="bold")
                cell.set_facecolor("#f0f0f0")
            elif row % 2 == 0:
                cell.set_facecolor("#f7f7f7")
            if column == 0:
                cell.set_text_props(ha="left")

        comparison_ax.text(
            0.02,
            0.15 - 8 / PANEL_HEIGHTS[1],
            "* NH = Northern Hemisphere",
            ha="left",
            va="top",
            fontsize=9,
            transform=comparison_ax.transAxes,
        )

        colorbar_map = ScalarMappable(norm=globe_norm, cmap=globe_cmap)
        colorbar_map.set_array([])
        colorbar_grid = globe_grid[2, :].subgridspec(
            1, 3, width_ratios=(1, 2, 1), wspace=0,
        )
        globe_colorbar_ax = fig.add_subplot(colorbar_grid[0, 1])
        globe_colorbar = fig.colorbar(colorbar_map, cax=globe_colorbar_ax,
                                      orientation="horizontal")
        globe_colorbar.set_label(
            "Comparison − Reference (W/m²)",
            fontsize=11, labelpad=1,
        )
        globe_colorbar.set_ticks([-250, -125, 0, 125, 250])
        globe_colorbar.ax.tick_params(labelsize=11, length=2)
        colorbar_position = globe_colorbar_ax.get_position()
        colorbar_height = 14 / fig.bbox.height
        colorbar_top_inset = 4 / fig.bbox.height
        globe_colorbar_ax.set_position((
            colorbar_position.x0,
            colorbar_position.y0 + colorbar_position.height
            - colorbar_top_inset,
            colorbar_position.width,
            colorbar_height,
        ))

        renderer = self.canvas.get_renderer()
        scale_bounds = globe_colorbar_ax.get_window_extent(renderer)
        tick_label_bounds = [
            label.get_window_extent(renderer)
            for label in globe_colorbar.ax.get_xticklabels()
            if label.get_visible()
        ]
        if tick_label_bounds:
            current_scale_gap = (
                scale_bounds.y0
                - max(bounds.y1 for bounds in tick_label_bounds)
            )
            current_tick_pad = (
                globe_colorbar.ax.xaxis.majorTicks[0].get_pad()
            )
            matched_tick_pad = current_tick_pad + (
                8.0 - current_scale_gap
            ) * 72 / fig.dpi
            globe_colorbar.ax.tick_params(
                labelsize=11, length=2, pad=matched_tick_pad,
            )
            colorbar_xaxis = globe_colorbar.ax.xaxis
            colorbar_xaxis._update_label_position(renderer)
            tick_label_bottom = min(
                label.get_window_extent(renderer).y0
                for label in globe_colorbar.ax.get_xticklabels()
                if label.get_visible()
            )
            caption_top = colorbar_xaxis.label.get_window_extent(
                renderer,
            ).y1
            current_caption_gap = tick_label_bottom - caption_top
            current_caption_pad = colorbar_xaxis.labelpad
            matched_caption_pad = current_caption_pad + (
                8.0 - current_caption_gap
            ) * 72 / fig.dpi
            globe_colorbar.set_label(
                "Comparison − Reference (W/m²)",
                fontsize=11, labelpad=matched_caption_pad,
            )
            colorbar_xaxis._update_label_position(renderer)
        if not self._single_column_layout:
            color_scale_top = globe_colorbar_ax.get_window_extent(
                renderer,
            ).y1
            legend_row_bounds = orbit_legend_ax.get_window_extent(renderer)
            comparison_legend_height = orbit_legend.get_window_extent(
                renderer,
            ).height
            event_legend_height = event_legend.get_window_extent(
                renderer,
            ).height
            legend_gap = 8.0
            comparison_center_y = (
                color_scale_top - comparison_legend_height / 2
            )
            event_center_y = (
                color_scale_top - comparison_legend_height - legend_gap
                - event_legend_height / 2
            )
            orbit_legend.set_bbox_to_anchor((
                sun_projected_x,
                (comparison_center_y - legend_row_bounds.y0)
                / legend_row_bounds.height,
            ), transform=orbit_legend_transform)
            event_legend.set_bbox_to_anchor((
                sun_projected_x,
                (event_center_y - legend_row_bounds.y0)
                / legend_row_bounds.height,
            ), transform=orbit_legend_transform)

        annual = fig.add_subplot(annual_cell)
        annual_panel = annual_cell.get_position(fig)
        panel_x, panel_y, panel_width, panel_height = annual_panel.bounds
        # Keep tick labels and axis titles inside the fixed Q4 boundaries.
        annual_bottom = 0.15
        annual_height = 0.73
        annual.set_position((
            panel_x + panel_width * 0.13,
            panel_y + panel_height * annual_bottom,
            panel_width * 0.84,
            panel_height * annual_height,
        ))
        annual.plot(result["annual_mean"], result["latitude_deg"], color="#4c78a8", lw=2,
                    label="Comparison")
        reference = simulate(reference_e, reference_tilt,
                             reference_phase, samples=360,
                             latitudes=result["latitude_deg"])
        annual.plot(reference["annual_mean"], reference["latitude_deg"], color="#777777",
                    lw=1.5, ls=(0, (6, 6)), label="Reference")
        annual.set(
            xlabel="W/m²", ylabel="Latitude",
        )
        annual.xaxis.label.set_fontsize(13.5)
        annual.yaxis.label.set_fontsize(13.5)
        for globe_ax in globe_axes:
            globe_title = globe_ax.title
            globe_title.set_fontproperties(
                annual.yaxis.label.get_fontproperties()
            )
            globe_title.set_color(annual.yaxis.label.get_color())
        annual.tick_params(axis="both", labelsize=12)
        annual.set_xlim(0, 450)
        annual.set_xticks(np.arange(0, 451, 50))
        annual.set_ylim(-90, 90)
        annual.set_yticks(np.arange(-90, 91, 30))
        comparison_x = np.asarray(result["annual_mean"])
        comparison_y = np.asarray(result["latitude_deg"])
        x_min, x_max = annual.get_xlim()
        y_min, y_max = annual.get_ylim()
        axes_width_points = (
            annual.get_position().width * fig.get_figwidth() * 72
        )
        comparison_halfwidth_axes = 1.0 / axes_width_points
        comparison_x_axes = (comparison_x - x_min) / (x_max - x_min)
        comparison_y_axes = (comparison_y - y_min) / (y_max - y_min)
        overlap_vertices = np.concatenate((
            np.column_stack((
                comparison_x_axes + comparison_halfwidth_axes,
                comparison_y_axes,
            )),
            np.column_stack((
                (comparison_x_axes - comparison_halfwidth_axes)[::-1],
                comparison_y_axes[::-1],
            )),
        ))
        overlap_clip = Polygon(
            overlap_vertices, closed=True, transform=annual.transAxes,
        )
        overlap_line, = annual.plot(
            reference["annual_mean"], reference["latitude_deg"],
            color="white", lw=2, ls=(0, (4.5, 4.5)),
            label="_nolegend_", zorder=4,
        )
        overlap_line.set_clip_path(overlap_clip)
        annual.grid(
            color="#b0b0b0", alpha=0.25,
            linestyle="-", linewidth=0.8,
        )
        legend_handles = [
            Line2D([], [], color="#4c78a8", lw=2, ls="-",
                   label="Comparison"),
            (
                Line2D([], [], color="#777777", lw=1.5, ls="-"),
                Line2D([], [], color="white", lw=1.5, ls="-"),
                Line2D([], [], color="#777777", lw=1.5, ls="-"),
            ),
        ]
        annual.legend(
            handles=legend_handles, labels=["Comparison", "Reference"],
            handler_map={tuple: HandlerTuple(ndivide=3, pad=0)},
            fontsize=11, handlelength=1.8,
            frameon=True, fancybox=True, framealpha=0.8,
            facecolor="#ffffff", edgecolor="#cccccc",
        )
        self.summary.configure(text=(
            "This simplified model calculates top-of-atmosphere insolation only. "
            "It does not simulate atmospheric effects, clouds, continents, or "
            "ice-sheet feedbacks."
        ))
        if self._single_column_layout:
            self.panel_separators = self._add_stacked_panel_separators(
                fig, (globe_cell, orbit_cell, comparison_cell, annual_cell),
            )
        else:
            self.panel_separators = self._add_panel_separators(
                fig, globe_cell, orbit_cell, comparison_cell, annual_cell,
            )
        if request_draw and not self._canvas_resizing:
            self.canvas.draw_idle()


def main():
    root = tk.Tk(className=APP_SHORT_NAME)
    MainWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
