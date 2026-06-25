"""
climate_data.py — climate ingestion + dengue transmission-suitability layer
===========================================================================
Free climate data from Open-Meteo (no API key): historical reanalysis +
16-day forecast, population-weighted to a national daily series, plus a
literature-based **temperature transmission-suitability index** for Aedes
aegypti and rainfall lead indicators.

Why a literature-based index (not a locally trained model): we don't yet have
years of local dengue history to fit lagged climate effects. The temperature
suitability curve (Briere form, Mordecai et al. 2017 thermal limits) encodes
established mosquito/virus biology, so it gives a principled climate signal
*now*, with no training data. As historical weekly dengue accrues, this can be
upgraded to a fitted distributed-lag model.

Pure standard library + numpy/pandas (no extra dependencies).
"""
from __future__ import annotations

import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

UA = "DengueDashboardClimate/1.0"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DAILY = ("temperature_2m_mean,temperature_2m_max,temperature_2m_min,"
         "precipitation_sum,relative_humidity_2m_mean")

# Approximate district centroids (lat, lon) for the 25 administrative districts.
DISTRICT_COORDS = {
    "Colombo": (6.93, 79.86), "Gampaha": (7.09, 80.00), "Kalutara": (6.58, 79.96),
    "Kandy": (7.29, 80.63), "Matale": (7.47, 80.62), "Nuwaraeliya": (6.97, 80.77),
    "Galle": (6.05, 80.22), "Matara": (5.95, 80.54), "Hambantota": (6.12, 81.12),
    "Jaffna": (9.66, 80.02), "Kilinochchi": (9.40, 80.40), "Mannar": (8.98, 79.91),
    "Vavuniya": (8.75, 80.50), "Mullaitivu": (9.27, 80.81),
    "Batticaloa": (7.71, 81.69), "Ampara": (7.30, 81.67),
    "Trincomalee": (8.59, 81.21), "Kurunegala": (7.49, 80.36),
    "Puttalam": (8.03, 79.83), "Anuradhapura": (8.31, 80.40),
    "Polonnaruwa": (7.94, 81.00), "Badulla": (6.99, 81.06),
    "Monaragala": (6.87, 81.35), "Ratnapura": (6.68, 80.40), "Kegalle": (7.25, 80.35),
}

# Aedes aegypti thermal limits for relative R0 (Mordecai et al. 2017, Briere fit)
_T0, _TM = 17.8, 34.6


def _get_json(url: str):
    req = Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=40, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _to_long(arr, names, kind) -> pd.DataFrame:
    if isinstance(arr, dict):                 # single-location safety
        arr = [arr]
    rows = []
    for name, loc in zip(names, arr):
        d = loc.get("daily", {})
        t = d.get("time", [])
        for i, day in enumerate(t):
            rows.append({
                "district": name, "date": day,
                "temp_mean": _at(d, "temperature_2m_mean", i),
                "temp_max": _at(d, "temperature_2m_max", i),
                "temp_min": _at(d, "temperature_2m_min", i),
                "precip": _at(d, "precipitation_sum", i),
                "humidity": _at(d, "relative_humidity_2m_mean", i),
                "_kind": kind,
            })
    return pd.DataFrame(rows)


def _at(d, key, i):
    v = d.get(key)
    return v[i] if v and i < len(v) and v[i] is not None else np.nan


def fetch_climate(folder: str | Path, start: str = "2026-01-01") -> pd.DataFrame | None:
    """Population-weighted national daily climate (observed + 16-day forecast).
    Returns a tidy DataFrame [date, temp_mean, temp_max, temp_min, precip,
    humidity, kind] or None if the service is unreachable."""
    names = list(DISTRICT_COORDS)
    lats = ",".join(f"{DISTRICT_COORDS[n][0]}" for n in names)
    lons = ",".join(f"{DISTRICT_COORDS[n][1]}" for n in names)
    today = date.today()
    arch_end = today - timedelta(days=5)

    frames = []
    try:
        if pd.Timestamp(start).date() <= arch_end:
            url = (f"{ARCHIVE}?latitude={lats}&longitude={lons}"
                   f"&start_date={start}&end_date={arch_end:%Y-%m-%d}"
                   f"&daily={DAILY}&timezone=auto")
            frames.append(_to_long(_get_json(url), names, "observed"))
    except Exception as e:  # noqa: BLE001
        print(f"[climate] archive fetch failed: {e}")
    try:
        url = (f"{FORECAST}?latitude={lats}&longitude={lons}"
               f"&daily={DAILY}&past_days=15&forecast_days=16&timezone=auto")
        frames.append(_to_long(_get_json(url), names, "forecast"))
    except Exception as e:  # noqa: BLE001
        print(f"[climate] forecast fetch failed: {e}")

    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return None

    long = pd.concat(frames, ignore_index=True)
    long["date"] = pd.to_datetime(long["date"])
    # prefer archive (observed) where it overlaps the forecast's past_days
    long = long.sort_values("_kind").drop_duplicates(["district", "date"],
                                                     keep="first")

    pop = _district_pop(folder)
    long = long.merge(pop, on="district", how="left")
    long["w"] = long["population"].fillna(long["population"].median())

    def _wavg(g, col):
        x = g[col].astype(float)
        w = g["w"].where(x.notna())
        return np.nan if w.sum() == 0 else float((x.fillna(0) * w).sum() / w.sum())

    out = []
    for day, g in long.groupby("date"):
        out.append({
            "date": day,
            "temp_mean": _wavg(g, "temp_mean"), "temp_max": _wavg(g, "temp_max"),
            "temp_min": _wavg(g, "temp_min"), "precip": _wavg(g, "precip"),
            "humidity": _wavg(g, "humidity"),
        })
    df = pd.DataFrame(out).sort_values("date").reset_index(drop=True)
    today_ts = pd.Timestamp(today)
    df["kind"] = np.where(df["date"] > today_ts, "forecast", "observed")
    return df


def _district_pop(folder: str | Path) -> pd.DataFrame:
    f = Path(folder) / "reference_population.csv"
    if not f.exists():
        f = Path(__file__).with_name("reference_population.csv")
    p = pd.read_csv(f)
    p = p[p.level == "district"][["area", "population"]].rename(
        columns={"area": "district"})
    return p


# --------------------------------------------------------------------------- #
# Transmission-suitability index + rainfall lead indicators
# --------------------------------------------------------------------------- #
def temperature_suitability(temp) -> np.ndarray:
    """Relative R0 vs temperature (Briere, Aedes aegypti), normalised to 0..1."""
    t = np.asarray(temp, dtype=float)
    b = np.where((t > _T0) & (t < _TM),
                 t * (t - _T0) * np.sqrt(np.clip(_TM - t, 0, None)), 0.0)
    grid = np.linspace(_T0, _TM, 400)
    peak = np.max(grid * (grid - _T0) * np.sqrt(_TM - grid))
    return np.clip(b / peak, 0, 1)


def add_signals(climate: pd.DataFrame) -> pd.DataFrame:
    df = climate.copy().sort_values("date").reset_index(drop=True)
    df["suitability"] = temperature_suitability(df["temp_mean"])
    # rainfall lead indicators: breeding -> adult -> transmission lag ~ 4-8 weeks
    df["rain_28d"] = df["precip"].rolling(28, min_periods=7).sum()
    df["rain_56d"] = df["precip"].rolling(56, min_periods=14).sum()
    df["suit_7d"] = df["suitability"].rolling(7, min_periods=3).mean()
    return df


def lag_correlation(incidence: pd.Series, climate_series: pd.Series,
                    max_lag_days: int = 84, step: int = 7) -> pd.DataFrame:
    """Correlate dengue incidence with a climate series shifted by various lags
    (climate leads). Exploratory on a short series — wide uncertainty."""
    inc = incidence.copy()
    inc.index = pd.to_datetime(inc.index)
    cs = climate_series.copy()
    cs.index = pd.to_datetime(cs.index)
    rows = []
    for lag in range(0, max_lag_days + 1, step):
        shifted = cs.shift(lag, freq="D")
        joined = pd.concat([inc, shifted], axis=1, join="inner").dropna()
        if len(joined) >= 8:
            r = float(np.corrcoef(joined.iloc[:, 0], joined.iloc[:, 1])[0, 1])
            rows.append({"lag_days": lag, "lag_weeks": lag // 7, "corr": r,
                         "n": len(joined)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    c = fetch_climate(folder)
    if c is None:
        print("Climate service unreachable.")
        raise SystemExit(1)
    s = add_signals(c)
    obs = s[s.kind == "observed"]
    fc = s[s.kind == "forecast"]
    print(f"Climate: {len(s)} days ({s.date.min().date()} -> {s.date.max().date()}), "
          f"{len(fc)} forecast days")
    last = obs.iloc[-1]
    print(f"Latest observed {last['date'].date()}: "
          f"temp {last['temp_mean']:.1f}°C, suitability {last['suitability']:.2f}, "
          f"28-day rain {last['rain_28d']:.0f} mm")
    if len(fc):
        print(f"Forecast suitability (next {len(fc)}d): "
              f"{fc['suitability'].mean():.2f} avg")
