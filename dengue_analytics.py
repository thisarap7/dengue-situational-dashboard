"""
dengue_analytics.py
===================
Turns parsed NaDSys snapshots into situational-awareness datasets:

  * daily new cases (cumulative snapshots differenced, normalised per day
    because the daily-update PDFs are not always 1 day apart)
  * surge / acceleration metrics per area
  * cumulative burden share
  * per-capita incidence (district & province, using 2024 census population)
  * a combined FLAG table  (Surging / High burden / High per-capita)

Everything is returned as tidy pandas DataFrames so the Streamlit app (or any
notebook) can render them directly.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

from dengue_parser import (
    parse_folder, to_area_frame, to_meta_frame,
    UNIT_TO_DISTRICT, DISTRICT_TO_PROVINCE,
)

REF_POP_FILE = "reference_population.csv"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_population(folder: str | Path) -> pd.DataFrame:
    f = Path(folder) / REF_POP_FILE
    if not f.exists():
        # also look next to this module
        f = Path(__file__).with_name(REF_POP_FILE)
    return pd.read_csv(f)


def build_all(folder: str | Path):
    """Parse a folder of PDFs and return the full bundle of datasets."""
    snaps = parse_folder(folder)
    if not snaps:
        raise FileNotFoundError(f"No parseable dengue PDFs found in {folder!r}")
    area = to_area_frame(snaps)
    meta = to_meta_frame(snaps)
    pop = load_population(folder)

    units = _add_daily(area[area.level == "unit"].copy(), key="area")
    provinces = _add_daily(area[area.level == "province"].copy(), key="area")
    districts = _aggregate_units_to_district(area[area.level == "unit"].copy())
    districts = _add_daily(districts, key="district")

    # attach population + per-capita
    districts = _attach_population(districts, pop, level="district", key="district")
    provinces = _attach_population(provinces, pop, level="province", key="area")

    bundle = dict(
        snapshots=snaps, area=area, meta=meta, population=pop,
        units=units, districts=districts, provinces=provinces,
    )
    return bundle


# --------------------------------------------------------------------------- #
# Daily-new-case differencing
# --------------------------------------------------------------------------- #
def _add_daily(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Add new_cases, day_gap, new_per_day for each area's time series."""
    df = df.sort_values([key, "date"]).copy()
    g = df.groupby(key, sort=False)
    df["prev_cum"] = g["cum_cases"].shift(1)
    df["prev_date"] = g["date"].shift(1)
    df["new_cases"] = df["cum_cases"] - df["prev_cum"]
    df["day_gap"] = (df["date"] - df["prev_date"]).dt.days
    df["new_per_day"] = df["new_cases"] / df["day_gap"]
    # cumulative share of national total on each date
    df["share_pct"] = df.groupby("date")["cum_cases"].transform(
        lambda s: 100 * s / s.sum())
    return df


def _aggregate_units_to_district(units: pd.DataFrame) -> pd.DataFrame:
    """Fold CMC->Colombo, Kalmunai->Ampara, NIHS->Kalutara so per-capita is
    computed on clean administrative districts."""
    u = units.copy()
    u["district"] = u["area"].map(lambda a: UNIT_TO_DISTRICT.get(a, a))
    agg = (u.groupby(["district", "date", "week"], as_index=False)["cum_cases"]
             .sum())
    agg["province"] = agg["district"].map(DISTRICT_TO_PROVINCE)
    return agg


def _attach_population(df: pd.DataFrame, pop: pd.DataFrame,
                       level: str, key: str) -> pd.DataFrame:
    p = pop[pop.level == level][["area", "population"]].rename(
        columns={"area": key})
    out = df.merge(p, on=key, how="left")
    out["cum_incidence_per100k"] = 1e5 * out["cum_cases"] / out["population"]
    out["daily_incidence_per100k"] = 1e5 * out["new_per_day"] / out["population"]
    return out


# --------------------------------------------------------------------------- #
# Flagging
# --------------------------------------------------------------------------- #
def latest_snapshot_table(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Latest row per area + that area's surge metrics vs its own baseline."""
    df = df.sort_values([key, "date"])
    rows = []
    for area, grp in df.groupby(key, sort=False):
        grp = grp.dropna(subset=["new_per_day"])
        latest = df[df[key] == area].iloc[-1]
        npd_series = grp["new_per_day"]
        latest_npd = npd_series.iloc[-1] if len(npd_series) else np.nan
        prev_npd = npd_series.iloc[-2] if len(npd_series) > 1 else np.nan
        baseline = (npd_series.iloc[:-1].median()
                    if len(npd_series) > 1 else np.nan)
        surge_ratio = latest_npd / baseline if baseline and baseline > 0 else np.nan
        accel = latest_npd - prev_npd if pd.notna(prev_npd) else np.nan
        row = {
            key: area,
            "cum_cases": int(latest["cum_cases"]),
            "share_pct": latest.get("share_pct", np.nan),
            "new_cases_latest": (latest["new_cases"]
                                 if pd.notna(latest["new_cases"]) else np.nan),
            "day_gap": latest.get("day_gap", np.nan),
            "new_per_day": latest_npd,
            "new_per_day_prev": prev_npd,
            "surge_ratio": surge_ratio,
            "acceleration": accel,
            "population": latest.get("population", np.nan),
            "cum_incidence_per100k": latest.get("cum_incidence_per100k", np.nan),
            "daily_incidence_per100k": latest.get("daily_incidence_per100k", np.nan),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def add_flags(tbl: pd.DataFrame, key: str,
              surge_ratio_thr: float = 1.3,
              min_new_cases: int = 15,
              burden_top_n: int = 5,
              percap_quantile: float = 0.75) -> pd.DataFrame:
    """Attach boolean flags + a human-readable flag string."""
    t = tbl.copy()

    # Surge: rising faster than its own recent baseline, above a noise floor
    t["flag_surge"] = (
        (t["surge_ratio"] >= surge_ratio_thr)
        & (t["new_cases_latest"] >= min_new_cases)
    ).fillna(False)

    # Burden: top-N cumulative contributors
    burden_cut = t.nlargest(burden_top_n, "cum_cases")[key].tolist()
    t["flag_burden"] = t[key].isin(burden_cut)

    # Per-capita: top quantile of cumulative incidence (if population known)
    if t["cum_incidence_per100k"].notna().any():
        thr = t["cum_incidence_per100k"].quantile(percap_quantile)
        t["flag_percap"] = t["cum_incidence_per100k"] >= thr
    else:
        t["flag_percap"] = False

    def _label(r):
        parts = []
        if r["flag_surge"]:
            parts.append("🔴 Surging")
        if r["flag_burden"]:
            parts.append("🟠 High burden")
        if r["flag_percap"]:
            parts.append("🟣 High per-capita")
        return "  ".join(parts)

    t["flags"] = t.apply(_label, axis=1)
    # priority score for sorting: surge weighted most, then per-capita, burden
    t["priority"] = (
        3 * t["flag_surge"].astype(int)
        + 2 * t["flag_percap"].astype(int)
        + 1 * t["flag_burden"].astype(int)
    )
    return t.sort_values(
        ["priority", "new_per_day"], ascending=[False, False]
    ).reset_index(drop=True)


def national_daily(meta: pd.DataFrame) -> pd.DataFrame:
    """National new cases per day from the YTD total series."""
    m = meta.sort_values("date").copy()
    m["new_cases"] = m["year_total"].diff()
    m["day_gap"] = m["date"].diff().dt.days
    m["new_per_day"] = m["new_cases"] / m["day_gap"]
    m["new_deaths"] = m["deaths"].diff()
    return m


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    b = build_all(folder)
    print(f"\nParsed {len(b['snapshots'])} snapshots "
          f"({b['meta'].date.min().date()} → {b['meta'].date.max().date()})\n")

    print("=== National ===")
    nat = national_daily(b["meta"])
    print(nat[["date", "year_total", "new_per_day", "deaths",
               "high_risk_moh", "avg_midnight_total"]].to_string(index=False))

    print("\n=== District flags (latest) ===")
    tbl = add_flags(latest_snapshot_table(b["districts"], "district"), "district")
    show = tbl[["district", "cum_cases", "share_pct", "new_per_day",
                "surge_ratio", "cum_incidence_per100k", "flags"]]
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(show.round(2).to_string(index=False))
