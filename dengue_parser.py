"""
dengue_parser.py
================
Deterministic parser for the Sri Lanka NaDSys "Current Status of Dengue"
daily-update PDFs (Epidemiology Unit / National Dengue Control Unit).

Each PDF is a one-page infographic whose numbers are CUMULATIVE year-to-date.
This module turns one PDF into a structured snapshot, and a folder of PDFs
into two tidy long-format DataFrames:

  * snapshots_areas : one row per (date, level, area)  with cumulative cases
  * snapshots_meta  : one row per date with the national KPIs

It uses pdfplumber's text layer (clean & deterministic on these PDFs) and a
dictionary of the known reporting units / provinces, so parsing does not depend
on the fragile column ordering of the original layout.
"""
from __future__ import annotations

import re
import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pdfplumber

# --------------------------------------------------------------------------- #
# Known reporting vocabulary
# --------------------------------------------------------------------------- #
# 25 administrative districts + 3 special RDHS reporting units used by the
# Epidemiology Unit's dengue surveillance:
#   CMC      = Colombo Municipal Council   (carved out of Colombo district)
#   Kalmunai = Kalmunai RDHS area          (carved out of Ampara district)
#   NIHS     = National Institute of Health Sciences area (Kalutara district)
# These three are separate partitions, so summing all 28 == national total.
DISTRICTS_25 = [
    "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwaraeliya",
    "Galle", "Hambantota", "Matara", "Jaffna", "Kilinochchi", "Mannar",
    "Vavuniya", "Mullaitivu", "Batticaloa", "Ampara", "Trincomalee",
    "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa", "Badulla",
    "Monaragala", "Ratnapura", "Kegalle",
]
SPECIAL_UNITS = ["CMC", "Kalmunai", "NIHS"]
DISTRICT_UNITS = DISTRICTS_25 + SPECIAL_UNITS

# Map each special reporting unit to the administrative district it sits in,
# so per-capita can be computed at clean district level.
UNIT_TO_DISTRICT = {
    "CMC": "Colombo",
    "Kalmunai": "Ampara",
    "NIHS": "Kalutara",
}

PROVINCES = [
    "North Western", "North Central", "Western", "Central", "Southern",
    "Northern", "Eastern", "Uva", "Sabaragamuwa",
]

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# District -> Province (standard Sri Lanka administrative mapping)
DISTRICT_TO_PROVINCE = {
    "Colombo": "Western", "Gampaha": "Western", "Kalutara": "Western",
    "Kandy": "Central", "Matale": "Central", "Nuwaraeliya": "Central",
    "Galle": "Southern", "Hambantota": "Southern", "Matara": "Southern",
    "Jaffna": "Northern", "Kilinochchi": "Northern", "Mannar": "Northern",
    "Vavuniya": "Northern", "Mullaitivu": "Northern",
    "Batticaloa": "Eastern", "Ampara": "Eastern", "Trincomalee": "Eastern",
    "Kurunegala": "North Western", "Puttalam": "North Western",
    "Anuradhapura": "North Central", "Polonnaruwa": "North Central",
    "Badulla": "Uva", "Monaragala": "Uva",
    "Ratnapura": "Sabaragamuwa", "Kegalle": "Sabaragamuwa",
}


def _alt(names) -> str:
    """Regex alternation, longest names first so e.g. 'North Western' wins
    over 'Western' and is consumed before the standalone 'Western'."""
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


_NUM = r"(\d[\d,]*)"
_PCT = r"([\d.]+)\s*%"

_RE_DATE = re.compile(r"As of\s+(\d{2})\.(\d{2})\.(\d{4})")
_RE_WEEK = re.compile(r"\[\s*Week\s+(\d+)\s*,\s*(\d{4})\s*\]")
_RE_YEAR_TOTAL = re.compile(r"\b(20\d{2})\s+" + _NUM + r"\s+District")
_RE_HIGHRISK = re.compile(r"High[\s\-]*risk\s+MOH\s+Areas\s+(\d+)")
_RE_AVGMID = re.compile(r"Average\s+Mid\s*-?\s*night\s+Total\s+(\d+)")
_RE_DEATHS = re.compile(r"Dengue\s+Deaths\s+in\s+\d{4}\s+(\d+)")
_RE_CFR = re.compile(r"Case\s+Fatality\s+Rate\s+([\d.]+)\s*%")

_RE_DISTRICT = re.compile(r"\b(" + _alt(DISTRICT_UNITS) + r")\s+" + _NUM + r"\s+" + _PCT)
_RE_PROVINCE = re.compile(r"\b(" + _alt(PROVINCES) + r")\s+" + _NUM + r"\s+" + _PCT)
_RE_MONTH = re.compile(r"\b(" + _alt(MONTHS) + r")\s+" + _NUM)


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


@dataclass
class Snapshot:
    date: _dt.date
    week: int | None
    year: int | None
    year_total: int | None
    high_risk_moh: int | None
    avg_midnight_total: int | None
    deaths: int | None
    cfr_pct: float | None
    months: dict = field(default_factory=dict)        # {'June': 8083, ...}
    districts: dict = field(default_factory=dict)      # {'Colombo': 8837, ...}
    provinces: dict = field(default_factory=dict)      # {'Western': 21451, ...}
    source_file: str = ""

    # ----- QC helpers ---------------------------------------------------- #
    def district_sum(self) -> int:
        return sum(self.districts.values())

    def province_sum(self) -> int:
        return sum(self.provinces.values())

    def qc(self) -> dict:
        return {
            "file": self.source_file,
            "date": self.date,
            "year_total": self.year_total,
            "n_districts": len(self.districts),
            "district_sum": self.district_sum(),
            "district_match": self.district_sum() == self.year_total,
            "n_provinces": len(self.provinces),
            "province_sum": self.province_sum(),
            "province_match": self.province_sum() == self.year_total,
        }


def parse_pdf(path: str | Path) -> Snapshot:
    """Parse a single NaDSys daily-update PDF into a Snapshot."""
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    m = _RE_DATE.search(text)
    if not m:
        raise ValueError(f"Could not find 'As of dd.mm.yyyy' date in {path.name}")
    dd, mm, yyyy = (int(x) for x in m.groups())
    date = _dt.date(yyyy, mm, dd)

    wk = _RE_WEEK.search(text)
    week = int(wk.group(1)) if wk else None
    yr = int(wk.group(2)) if wk else yyyy

    yt = _RE_YEAR_TOTAL.search(text)
    year_total = _to_int(yt.group(2)) if yt else None

    def _grab_int(rx):
        mm = rx.search(text)
        return int(mm.group(1)) if mm else None

    def _grab_float(rx):
        mm = rx.search(text)
        return float(mm.group(1)) if mm else None

    districts = {name: _to_int(num) for name, num, _pct in _RE_DISTRICT.findall(text)}
    provinces = {name: _to_int(num) for name, num, _pct in _RE_PROVINCE.findall(text)}
    months = {name: _to_int(num) for name, num in _RE_MONTH.findall(text)}

    return Snapshot(
        date=date,
        week=week,
        year=yr,
        year_total=year_total,
        high_risk_moh=_grab_int(_RE_HIGHRISK),
        avg_midnight_total=_grab_int(_RE_AVGMID),
        deaths=_grab_int(_RE_DEATHS),
        cfr_pct=_grab_float(_RE_CFR),
        months=months,
        districts=districts,
        provinces=provinces,
        source_file=path.name,
    )


def parse_folder(folder: str | Path, pattern: str = "*.pdf") -> list[Snapshot]:
    """Parse every matching PDF in a folder, sorted by reporting date."""
    folder = Path(folder)
    snaps = []
    for p in sorted(folder.glob(pattern)):
        try:
            snaps.append(parse_pdf(p))
        except Exception as e:  # noqa: BLE001  - surface, but keep going
            print(f"[warn] failed to parse {p.name}: {e}")
    snaps.sort(key=lambda s: s.date)
    return snaps


# --------------------------------------------------------------------------- #
# Tidy long-format builders
# --------------------------------------------------------------------------- #
def to_area_frame(snaps: list[Snapshot]) -> pd.DataFrame:
    """One row per (date, level, area) with cumulative cases & reported share."""
    rows = []
    for s in snaps:
        for name, cases in s.districts.items():
            rows.append(dict(date=s.date, week=s.week, level="unit",
                             area=name,
                             district=UNIT_TO_DISTRICT.get(name, name),
                             province=DISTRICT_TO_PROVINCE.get(
                                 UNIT_TO_DISTRICT.get(name, name)),
                             cum_cases=cases, source=s.source_file))
        for name, cases in s.provinces.items():
            rows.append(dict(date=s.date, week=s.week, level="province",
                             area=name, district=None, province=name,
                             cum_cases=cases, source=s.source_file))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["level", "area", "date"]).reset_index(drop=True)


def to_meta_frame(snaps: list[Snapshot]) -> pd.DataFrame:
    """One row per date with national KPIs and monthly cumulative totals."""
    rows = []
    for s in snaps:
        row = dict(date=s.date, week=s.week, year=s.year,
                   year_total=s.year_total, high_risk_moh=s.high_risk_moh,
                   avg_midnight_total=s.avg_midnight_total,
                   deaths=s.deaths, cfr_pct=s.cfr_pct, source=s.source_file)
        for mth, val in s.months.items():
            row[f"month_{mth}"] = val
        rows.append(row)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    snaps = parse_folder(folder)
    print(f"Parsed {len(snaps)} PDF(s) from {folder!r}\n")
    qc = pd.DataFrame([s.qc() for s in snaps])
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(qc.to_string(index=False))
