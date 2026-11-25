"""Small, self-contained Milanković-cycle model for the MiLaS prototype."""

from __future__ import annotations

from datetime import date

import numpy as np

SOLAR_CONSTANT = 1361.0  # W m⁻² at 1 AU
YEAR_DAYS = 365.256
TODAY_ECCENTRICITY = 0.0167
TODAY_OBLIQUITY_DEG = 23.44
TODAY_WINTER_SOLSTICE_DEG = 347.0


def eccentric_anomaly(mean_anomaly: np.ndarray, eccentricity: float) -> np.ndarray:
    """Solve Kepler's equation with Newton iteration (angles in radians)."""
    mean_anomaly = np.asarray(mean_anomaly, dtype=float)
    e = float(eccentricity)
    E = mean_anomaly.copy() if e < 0.8 else np.full_like(mean_anomaly, np.pi)
    for _ in range(12):
        delta = (E - e * np.sin(E) - mean_anomaly) / (1 - e * np.cos(E))
        E -= delta
        if np.max(np.abs(delta), initial=0.0) < 1e-13:
            break
    return E


def orbital_state(eccentricity: float, winter_solstice_deg: float,
                  samples: int = 360) -> dict[str, np.ndarray]:
    """Return one year sampled uniformly in time, with perihelion at day zero.

    ``winter_solstice_deg`` is the Earth's true anomaly at northern winter
    solstice, measured from perihelion in the direction of orbital motion.
    """
    e = float(eccentricity)
    if not 0 <= e < 1:
        raise ValueError("eccentricity must be in [0, 1)")
    M = np.linspace(0.0, 2 * np.pi, samples, endpoint=False)
    E = eccentric_anomaly(M, e)
    nu = 2 * np.arctan2(np.sqrt(1 + e) * np.sin(E / 2),
                        np.sqrt(1 - e) * np.cos(E / 2))
    radius = 1 - e * np.cos(E)  # AU, with semi-major axis a=1 AU
    gamma = np.deg2rad(winter_solstice_deg)
    obliquity = np.deg2rad(23.44)  # replaced by radiation() caller as needed
    declination = np.arcsin(-np.sin(obliquity) * np.cos(nu - gamma))
    return {"mean_anomaly": M, "eccentric_anomaly": E, "true_anomaly": nu,
            "radius_au": radius, "day": M * YEAR_DAYS / (2 * np.pi),
            "declination_rad": declination}


def daily_mean_insolation(latitude_deg: np.ndarray, declination_rad: np.ndarray,
                          radius_au: np.ndarray) -> np.ndarray:
    """Daily-mean top-of-atmosphere insolation, W m⁻².

    Output axes are latitude × time. The formula includes polar day/night and
    assumes a spherical Earth, a point Sun and no atmosphere.
    """
    lat = np.deg2rad(np.asarray(latitude_deg, dtype=float))[:, None]
    dec = np.asarray(declination_rad, dtype=float)[None, :]
    r = np.asarray(radius_au, dtype=float)[None, :]
    arg = np.clip(-np.tan(lat) * np.tan(dec), -1.0, 1.0)
    hour_angle = np.arccos(arg)
    q = SOLAR_CONSTANT / (np.pi * r**2) * (
        hour_angle * np.sin(lat) * np.sin(dec)
        + np.cos(lat) * np.cos(dec) * np.sin(hour_angle)
    )
    return np.maximum(q, 0.0)


def instantaneous_insolation(latitude_deg: np.ndarray, longitude_deg: np.ndarray,
                             declination_rad: float, radius_au: float) -> np.ndarray:
    """Instantaneous top-of-atmosphere sunlight on a globe at local noon.

    Longitude zero is the subsolar meridian. The result is latitude × longitude
    in W m⁻², with the night side set to zero.
    """
    lat = np.deg2rad(np.asarray(latitude_deg, dtype=float))[:, None]
    lon = np.deg2rad(np.asarray(longitude_deg, dtype=float))[None, :]
    dec = float(declination_rad)
    cosine_zenith = np.cos(lat) * np.cos(lon) * np.cos(dec) + np.sin(lat) * np.sin(dec)
    return SOLAR_CONSTANT / float(radius_au) ** 2 * np.maximum(cosine_zenith, 0.0)


def season_orientation(obliquity_deg: float, winter_solstice_deg: float,
                       true_anomaly_rad: float
                       ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Return the solar declination and local-to-ecliptic globe frame.

    The returned vectors are the spin axis, the subsolar meridian, and east in
    ecliptic coordinates. They orient the textured sphere and its axis in a
    fixed orbital frame, so precession visibly changes the axis direction.
    """
    eps = np.deg2rad(float(obliquity_deg))
    gamma = np.deg2rad(float(winter_solstice_deg))
    nu = float(true_anomaly_rad)
    spin_axis = np.array([
        np.sin(eps) * np.cos(gamma),
        np.sin(eps) * np.sin(gamma),
        np.cos(eps),
    ])
    sun_direction = np.array([-np.cos(nu), -np.sin(nu), 0.0])
    declination = np.arcsin(np.clip(np.dot(spin_axis, sun_direction), -1.0, 1.0))
    noon_meridian = sun_direction - np.dot(sun_direction, spin_axis) * spin_axis
    noon_meridian /= np.linalg.norm(noon_meridian)
    east = np.cross(spin_axis, noon_meridian)
    return declination, spin_axis, noon_meridian, east


def simulate(eccentricity: float, obliquity_deg: float,
             winter_solstice_deg: float, *, samples: int = 360,
             latitudes: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Compute orbit, seasonal declination and daily-mean insolation."""
    if not 0 <= obliquity_deg <= 90:
        raise ValueError("obliquity must be in [0, 90] degrees")
    state = orbital_state(eccentricity, winter_solstice_deg, samples)
    eps = np.deg2rad(obliquity_deg)
    state["declination_rad"] = np.arcsin(
        -np.sin(eps) * np.cos(state["true_anomaly"] - np.deg2rad(winter_solstice_deg))
    )
    if latitudes is None:
        latitudes = np.linspace(-90, 90, 73)
    state["latitude_deg"] = np.asarray(latitudes, dtype=float)
    state["insolation"] = daily_mean_insolation(
        state["latitude_deg"], state["declination_rad"], state["radius_au"]
    )
    state["annual_mean"] = np.mean(state["insolation"], axis=1)
    return state


def orbit_xy(eccentricity: float, true_anomaly: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Heliocentric ellipse in AU, focus (the Sun) at the origin."""
    nu = np.asarray(true_anomaly, dtype=float)
    p = 1 - eccentricity**2
    r = p / (1 + eccentricity * np.cos(nu))
    return r * np.cos(nu), r * np.sin(nu)


def true_anomaly_on_date(eccentricity: float, winter_solstice_deg: float,
                         current_date: date) -> float:
    """Return the orbit's true anomaly on a calendar date.

    The winter solstice is used as the seasonal anchor for each independent
    orbit. Its true anomaly is measured from perihelion.
    """
    winter_solstice = date(current_date.year, 12, 21)
    if current_date < winter_solstice:
        winter_solstice = date(current_date.year - 1, 12, 21)
    elapsed_days = (current_date - winter_solstice).days

    e = float(eccentricity)
    nu0 = np.deg2rad(winter_solstice_deg)
    eccentric_anomaly0 = 2 * np.arctan2(
        np.sqrt(1 - e) * np.sin(nu0 / 2),
        np.sqrt(1 + e) * np.cos(nu0 / 2),
    )
    mean_anomaly0 = eccentric_anomaly0 - e * np.sin(eccentric_anomaly0)
    mean_anomaly = mean_anomaly0 + 2 * np.pi * elapsed_days / 365.2422
    mean_anomaly = (mean_anomaly + np.pi) % (2 * np.pi) - np.pi

    eccentric_anomaly = mean_anomaly
    for _ in range(8):
        eccentric_anomaly -= (
            eccentric_anomaly - e * np.sin(eccentric_anomaly) - mean_anomaly
        ) / (1 - e * np.cos(eccentric_anomaly))

    true_anomaly = 2 * np.arctan2(
        np.sqrt(1 + e) * np.sin(eccentric_anomaly / 2),
        np.sqrt(1 - e) * np.cos(eccentric_anomaly / 2),
    )
    return float(true_anomaly % (2 * np.pi))


def quick_compare_values(preset: str, eccentricity: float, obliquity_deg: float,
                        winter_solstice_deg: float) -> tuple[float, float, float]:
    """Return preset values while preserving parameters outside its scope."""
    e, tilt, phase = eccentricity, obliquity_deg, winter_solstice_deg
    if preset == "today":
        return TODAY_ECCENTRICITY, TODAY_OBLIQUITY_DEG, TODAY_WINTER_SOLSTICE_DEG
    if preset == "emin":
        return 0.001, tilt, phase
    if preset == "emax":
        return 0.0679, tilt, phase
    if preset == "tiltmin":
        return e, 22.1, phase
    if preset == "tiltmax":
        return e, 24.5, phase
    if preset == "opposite":
        return e, tilt, (phase + 180) % 360
    raise ValueError(f"unknown quick comparison preset: {preset}")
