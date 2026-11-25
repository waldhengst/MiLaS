from datetime import date

import numpy as np

from milas.model import (
    eccentric_anomaly, instantaneous_insolation, orbit_xy, season_orientation,
    quick_compare_values, simulate, true_anomaly_on_date,
)


def test_kepler_solver_residual_and_circle_radius():
    mean = np.linspace(0, 2 * np.pi, 101)
    e = 0.0
    E = eccentric_anomaly(mean, e)
    np.testing.assert_allclose(E, mean, atol=1e-12)
    x, y = orbit_xy(e, mean)
    np.testing.assert_allclose(np.hypot(x, y), 1.0, atol=1e-12)


def test_ellipse_perihelion_and_aphelion():
    e = 0.2
    x, y = orbit_xy(e, np.array([0.0, np.pi]))
    np.testing.assert_allclose(np.hypot(x, y), [1 - e, 1 + e])


def test_earth_orbit_position_uses_date_and_each_parameter_set_independently():
    winter_date = date(2024, 12, 21)
    summer_date = date(2025, 6, 21)
    comparison = (0.0679, 347.0)
    reference = (0.0167, 91.0)

    def position(parameters, current_date):
        eccentricity, winter_solstice_deg = parameters
        anomaly = true_anomaly_on_date(
            eccentricity, winter_solstice_deg, current_date,
        )
        x, y = orbit_xy(eccentricity, np.array([anomaly]))
        return np.array([x[0], y[0]])

    comparison_winter = position(comparison, winter_date)
    comparison_summer = position(comparison, summer_date)
    reference_winter = position(reference, winter_date)

    assert not np.allclose(comparison_winter, comparison_summer)
    assert not np.allclose(comparison_winter, reference_winter)
    np.testing.assert_allclose(
        true_anomaly_on_date(*comparison, winter_date), np.deg2rad(347.0),
    )
    np.testing.assert_allclose(
        true_anomaly_on_date(*reference, winter_date), np.deg2rad(91.0),
    )
    expected_comparison = orbit_xy(
        comparison[0], np.array([np.deg2rad(comparison[1])]),
    )
    expected_reference = orbit_xy(
        reference[0], np.array([np.deg2rad(reference[1])]),
    )
    np.testing.assert_allclose(
        comparison_winter, [expected_comparison[0][0], expected_comparison[1][0]],
    )
    np.testing.assert_allclose(
        reference_winter, [expected_reference[0][0], expected_reference[1][0]],
    )


def test_simulation_is_finite_and_parameter_bounds_are_respected():
    result = simulate(0.0679, 24.5, 347, samples=180)
    assert result["insolation"].shape == (73, 180)
    assert np.isfinite(result["insolation"]).all()
    assert (result["insolation"] >= 0).all()
    assert np.max(np.abs(result["declination_rad"])) <= np.deg2rad(24.5) + 1e-12


def test_precession_phase_reverses_seasonal_pattern_at_same_orbit():
    first = simulate(0.05, 23.44, 0, samples=240)
    second = simulate(0.05, 23.44, 180, samples=240)
    assert not np.allclose(first["insolation"], second["insolation"])
    np.testing.assert_allclose(first["radius_au"], second["radius_au"])


def test_quick_comparisons_preserve_parameters_outside_their_scope():
    current = (0.0412, 24.12, 91.0)
    assert quick_compare_values("emin", *current) == (0.001, 24.12, 91.0)
    assert quick_compare_values("emax", *current) == (0.0679, 24.12, 91.0)
    assert quick_compare_values("tiltmin", *current) == (0.0412, 22.1, 91.0)
    assert quick_compare_values("tiltmax", *current) == (0.0412, 24.5, 91.0)
    assert quick_compare_values("opposite", *current) == (0.0412, 24.12, 271.0)
    assert quick_compare_values("today", *current) == (0.0167, 23.44, 347.0)


def test_instantaneous_insolation_shows_day_night_and_seasonal_poles():
    latitudes = np.array([-90.0, 0.0, 90.0])
    longitudes = np.array([0.0, 90.0, 180.0])
    summer = instantaneous_insolation(latitudes, longitudes, np.deg2rad(23.44), 1.0)
    winter = instantaneous_insolation(latitudes, longitudes, np.deg2rad(-23.44), 1.0)
    assert summer.shape == (3, 3)
    assert summer[0, 0] == 0 and winter[2, 0] == 0
    assert summer[2, 0] > 0 and winter[0, 0] > 0
    assert summer[1, 2] == 0 and winter[1, 2] == 0


def test_season_orientation_tracks_declination_and_obliquity():
    tilt = 24.5
    eps = np.deg2rad(tilt)
    phase = 37.0
    winter_nu = np.deg2rad(phase)
    winter_dec, winter_axis, winter_noon, winter_east = season_orientation(
        tilt, phase, winter_nu
    )
    summer_dec, summer_axis, summer_noon, summer_east = season_orientation(
        tilt, phase, winter_nu + np.pi
    )
    np.testing.assert_allclose(winter_dec, -eps)
    np.testing.assert_allclose(summer_dec, eps)
    for axis, noon, east in ((winter_axis, winter_noon, winter_east),
                             (summer_axis, summer_noon, summer_east)):
        np.testing.assert_allclose(np.linalg.norm(axis), 1.0)
        np.testing.assert_allclose(np.linalg.norm(noon), 1.0)
        np.testing.assert_allclose(np.linalg.norm(east), 1.0)
        np.testing.assert_allclose(axis[2], np.cos(eps))
        np.testing.assert_allclose(np.dot(axis, noon), 0.0, atol=1e-12)
        np.testing.assert_allclose(np.dot(axis, east), 0.0, atol=1e-12)
    opposite_phase_axis = season_orientation(
        tilt, phase + 180, winter_nu + np.pi
    )[1]
    np.testing.assert_allclose(opposite_phase_axis[:2], -winter_axis[:2])


def test_counterphase_flips_signed_tilt_without_moving_seasonal_view():
    tilt = 23.0
    season_nu = np.deg2rad(347.0)
    normal = season_orientation(tilt, 347.0, season_nu)
    counterphase = season_orientation(tilt, 167.0, season_nu)

    np.testing.assert_allclose(normal[0], -np.deg2rad(tilt))
    np.testing.assert_allclose(counterphase[0], np.deg2rad(tilt))
    np.testing.assert_allclose(counterphase[1][:2], -normal[1][:2])
    np.testing.assert_allclose(counterphase[1][2], normal[1][2])
    # Both states use the same Sun direction, so the view does not turn 180°.
    for state in (normal, counterphase):
        declination, axis, noon, _east = state
        sun_direction = np.cos(declination) * noon + np.sin(declination) * axis
        np.testing.assert_allclose(
            sun_direction, [-np.cos(season_nu), -np.sin(season_nu), 0.0],
            atol=1e-12,
        )
