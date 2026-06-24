"""
Dengue Situational Dashboard — Sri Lanka (NaDSys daily updates)
==============================================================
Drop the "Daily Update YYYY. MM. DD.pdf" files into this folder (or upload them
from the sidebar) and run:

    streamlit run app.py

The app parses every PDF, accumulates the daily snapshots, and builds a
situational dashboard: national trends, area flagging (Surging / High burden /
High per-capita), surge watch, burden, per-capita incidence, a district map,
and CSV/Excel export.
"""
from __future__ import annotations

import io
import json
import hmac
from pathlib import Path

import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

from dengue_analytics import (
    build_all, latest_snapshot_table, add_flags, national_daily,
)
from dengue_parser import DISTRICT_TO_PROVINCE

APP_DIR = Path(__file__).parent
GEOJSON = APP_DIR / "lk_districts.geojson"

st.set_page_config(page_title="Dengue Situational Dashboard — Sri Lanka",
                   page_icon="🦟", layout="wide")

# --------------------------------------------------------------------------- #
# Data loading (cached on the set of PDFs + their mtimes)
# --------------------------------------------------------------------------- #
def _signature(folder: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime)
                        for p in folder.glob("*.pdf")))


@st.cache_data(show_spinner="Parsing PDFs…")
def load_bundle(folder_str: str, _sig: tuple):
    return build_all(folder_str)


def fmt(n, dp=0):
    if n is None or (isinstance(n, float) and np.isnan(n)):
        return "—"
    return f"{n:,.{dp}f}"


# --------------------------------------------------------------------------- #
# Sidebar — admin (gated upload) + flag thresholds
# --------------------------------------------------------------------------- #
# The data folder is fixed to the app directory (PDFs ship in the repo). We do
# NOT expose a free-text path box on the public app — that would let any visitor
# probe the server's filesystem.
folder = APP_DIR


def _get_secret(key):
    """Read a Streamlit secret, returning None if no secrets are configured."""
    try:
        return st.secrets[key]
    except Exception:
        return None


def render_admin_and_get_is_admin() -> bool:
    """Viewing is open to everyone. Uploading new PDFs is restricted to people
    who know the admin passcode (set as `admin_password` in the app's Secrets).
    The uploader widget is only created for an authenticated admin, so it is not
    reachable by ordinary visitors — this is a server-side gate, not a UI hide."""
    admin_pw = _get_secret("admin_password")
    with st.sidebar.expander("🔒 Data admin", expanded=False):
        if not admin_pw:
            st.caption(
                "Uploads are disabled. New daily PDFs are added by committing "
                "them to the GitHub repo. To let trusted editors upload from "
                "here instead, set an `admin_password` in **Settings → Secrets** "
                "on Streamlit Cloud.")
            return False

        entered = st.text_input("Admin passcode", type="password",
                                key="admin_pw_input",
                                placeholder="Enter passcode to enable upload")
        if not entered:
            st.caption("Viewing is open to everyone; uploading requires the "
                       "admin passcode.")
            return False
        if not hmac.compare_digest(str(entered), str(admin_pw)):
            st.error("Incorrect passcode.")
            return False

        st.success("Admin unlocked — you can add PDFs.")
        up = st.file_uploader("Add daily-update PDF(s)", type="pdf",
                              accept_multiple_files=True)
        if up:
            added = 0
            for f in up:
                safe = Path(f.name).name              # strip any path component
                if not safe.lower().endswith(".pdf"):
                    continue
                (folder / safe).write_bytes(f.getbuffer())
                added += 1
            st.success(f"Added {added} PDF(s).")
            st.cache_data.clear()
        if st.button("🔄 Re-scan folder"):
            st.cache_data.clear()
        st.caption("Note: on the hosted app the filesystem is temporary, so "
                   "uploads last only until the app restarts. Commit PDFs to "
                   "the repo for a permanent update.")
        return True


st.sidebar.title("🦟 Dengue Dashboard")
is_admin = render_admin_and_get_is_admin()

st.sidebar.divider()
st.sidebar.subheader("Flag thresholds")
surge_thr = st.sidebar.slider("Surge ratio (vs own baseline)", 1.0, 3.0, 1.3, 0.1)
min_new = st.sidebar.slider("Min new cases (noise floor)", 0, 100, 15, 5)
burden_n = st.sidebar.slider("High-burden: top N areas", 3, 12, 5, 1)
percap_q = st.sidebar.slider("High per-capita: top quantile", 0.5, 0.95, 0.75, 0.05)

# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
try:
    sig = _signature(folder)
except FileNotFoundError:
    st.error(f"Folder not found: {folder}")
    st.stop()

if not sig:
    st.title("Dengue Situational Dashboard")
    st.warning("No daily-update PDFs are bundled with the app yet. Add them to "
               "the repository (or, if you are an editor, unlock **Data admin** "
               "in the sidebar and upload them).")
    st.stop()

bundle = load_bundle(str(folder), sig)
meta = bundle["meta"]
nat = national_daily(meta)
latest = nat.iloc[-1]
prev = nat.iloc[-2] if len(nat) > 1 else None
as_of = latest["date"].date()


def flag_table(level: str):
    src = {"district": bundle["districts"],
           "province": bundle["provinces"],
           "unit": bundle["units"]}[level]
    key = {"district": "district", "province": "area", "unit": "area"}[level]
    tbl = latest_snapshot_table(src, key)
    tbl = add_flags(tbl, key, surge_ratio_thr=surge_thr, min_new_cases=min_new,
                    burden_top_n=burden_n, percap_quantile=percap_q)
    return tbl.rename(columns={key: "area"})


# --------------------------------------------------------------------------- #
# Header + KPIs
# --------------------------------------------------------------------------- #
st.title("Dengue Situational Dashboard — Sri Lanka")
st.caption(f"Source: NaDSys surveillance (Epidemiology Unit). "
           f"Latest snapshot: **{as_of:%d %B %Y}** · "
           f"{len(bundle['snapshots'])} daily updates loaded "
           f"({nat.date.min():%d %b} → {nat.date.max():%d %b %Y}). "
           f"All counts are cumulative year-to-date; daily figures are derived "
           f"by differencing consecutive snapshots.")


def delta(cur, pre, dp=0, inverse=False):
    if pre is None or pd.isna(cur) or pd.isna(pre):
        return None
    d = cur - pre
    return f"{d:+,.{dp}f}"


k = st.columns(6)
k[0].metric("Cumulative cases (YTD)", fmt(latest["year_total"]),
            delta(latest["year_total"], prev["year_total"] if prev is not None else None))
k[1].metric("New cases / day (latest)", fmt(latest["new_per_day"], 0),
            help="Year-to-date total differenced over the gap since the previous snapshot.")
k[2].metric("Cumulative deaths", fmt(latest["deaths"]),
            delta(latest["deaths"], prev["deaths"] if prev is not None else None))
k[3].metric("Case fatality rate", f"{latest['cfr_pct']:.2f}%"
            if pd.notna(latest["cfr_pct"]) else "—")
k[4].metric("High-risk MOH areas", fmt(latest["high_risk_moh"]),
            delta(latest["high_risk_moh"], prev["high_risk_moh"] if prev is not None else None))
k[5].metric("Avg midnight inpatients", fmt(latest["avg_midnight_total"]),
            delta(latest["avg_midnight_total"], prev["avg_midnight_total"] if prev is not None else None))

# Auto situational narrative
dtbl = flag_table("district")
surging = dtbl[dtbl.flag_surge]["area"].tolist()
top_percap = dtbl.sort_values("cum_incidence_per100k", ascending=False).head(3)
narrative = [
    f"As of **{as_of:%d %b %Y}**, Sri Lanka has recorded "
    f"**{int(latest['year_total']):,}** cumulative dengue cases in {as_of.year}, "
    f"with **{int(latest['deaths'])}** deaths (CFR {latest['cfr_pct']:.2f}%)."
]
if pd.notna(latest["new_per_day"]):
    narrative.append(
        f"Cases are accruing at ~**{latest['new_per_day']:,.0f}/day** "
        f"in the latest interval.")
if surging:
    narrative.append(f"⚠️ **Surging districts:** {', '.join(surging)}.")
narrative.append(
    "**Highest per-capita burden:** "
    + ", ".join(f"{r.area} ({r.cum_incidence_per100k:,.0f}/100k)"
                for r in top_percap.itertuples()) + ".")
st.info(" ".join(narrative))

# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
tab_over, tab_flags, tab_surge, tab_burden, tab_pc, tab_map, tab_data = st.tabs(
    ["📈 National trends", "🚩 Area flags", "🔴 Surge watch",
     "🟠 Burden", "🟣 Per-capita", "🗺️ Map", "📋 Data & export"])

# ---- National trends ------------------------------------------------------ #
with tab_over:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Cumulative cases (YTD)")
        st.altair_chart(
            alt.Chart(nat).mark_line(point=True).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("year_total:Q", title="Cumulative cases"),
                tooltip=["date:T", "year_total:Q"]
            ).properties(height=260), use_container_width=True)
    with c2:
        st.subheader("New cases per day (derived)")
        st.altair_chart(
            alt.Chart(nat.dropna(subset=["new_per_day"])).mark_bar().encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("new_per_day:Q", title="New cases / day"),
                tooltip=["date:T", alt.Tooltip("new_per_day:Q", format=",.0f"),
                         alt.Tooltip("new_cases:Q", title="new in interval")]
            ).properties(height=260), use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Monthly cumulative totals")
        mcols = [c for c in meta.columns if c.startswith("month_")]
        last = meta.sort_values("date").iloc[-1]
        order = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
        md = pd.DataFrame({
            "month": [c.replace("month_", "") for c in mcols],
            "cases": [last[c] for c in mcols]})
        md["o"] = md["month"].map({m: i for i, m in enumerate(order)})
        md = md.sort_values("o")
        st.altair_chart(
            alt.Chart(md).mark_bar().encode(
                x=alt.X("month:N", sort=list(md["month"]), title=None),
                y=alt.Y("cases:Q", title="Cases"),
                tooltip=["month", "cases"]
            ).properties(height=260), use_container_width=True)
        st.caption("Current month is still accumulating, so it grows each day.")
    with c4:
        st.subheader("Deaths · high-risk MOH · midnight inpatients")
        long = nat.melt(id_vars="date",
                        value_vars=["deaths", "high_risk_moh", "avg_midnight_total"],
                        var_name="metric", value_name="value").dropna()
        label = {"deaths": "Cumulative deaths",
                 "high_risk_moh": "High-risk MOH areas",
                 "avg_midnight_total": "Avg midnight inpatients"}
        long["metric"] = long["metric"].map(label)
        st.altair_chart(
            alt.Chart(long).mark_line(point=True).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("value:Q", title=None),
                color=alt.Color("metric:N", title=None,
                                legend=alt.Legend(orient="bottom")),
                tooltip=["date:T", "metric:N", "value:Q"]
            ).properties(height=260), use_container_width=True)

# ---- Area flags ----------------------------------------------------------- #
with tab_flags:
    level = st.radio("Level", ["district", "province", "unit"],
                     horizontal=True, key="flag_level",
                     help="'unit' = the 28 RDHS reporting units as printed "
                          "(incl. CMC, Kalmunai, NIHS). District folds those in.")
    tbl = flag_table(level)
    st.markdown("**Legend:** 🔴 Surging (rising vs own baseline) · "
                "🟠 High burden (top-N cumulative) · 🟣 High per-capita "
                "(top incidence quantile)")

    show = tbl[["area", "flags", "cum_cases", "share_pct", "new_cases_latest",
                "new_per_day", "surge_ratio", "cum_incidence_per100k",
                "daily_incidence_per100k"]].copy()
    st.dataframe(
        show, use_container_width=True, hide_index=True,
        column_config={
            "area": "Area",
            "flags": st.column_config.TextColumn("Flags", width="medium"),
            "cum_cases": st.column_config.NumberColumn("Cum. cases", format="%d"),
            "share_pct": st.column_config.NumberColumn("Share %", format="%.2f"),
            "new_cases_latest": st.column_config.NumberColumn("New (last interval)", format="%.0f"),
            "new_per_day": st.column_config.NumberColumn("New/day", format="%.1f"),
            "surge_ratio": st.column_config.NumberColumn("Surge ×", format="%.2f"),
            "cum_incidence_per100k": st.column_config.NumberColumn("Cum/100k", format="%.0f"),
            "daily_incidence_per100k": st.column_config.NumberColumn("Daily/100k", format="%.2f"),
        })
    n_flag = (tbl["flags"] != "").sum()
    st.caption(f"{n_flag} of {len(tbl)} {level}s carry at least one flag.")

# ---- Surge watch ---------------------------------------------------------- #
with tab_surge:
    st.subheader("Where are new cases accelerating?")
    level2 = st.radio("Level", ["district", "province"], horizontal=True,
                      key="surge_level")
    tbl = flag_table(level2)
    cand = tbl[tbl["new_cases_latest"] >= min_new].copy()
    cand = cand.sort_values("surge_ratio", ascending=False)
    st.altair_chart(
        alt.Chart(cand.head(15)).mark_bar().encode(
            x=alt.X("surge_ratio:Q", title="Surge ratio (latest ÷ baseline new/day)"),
            y=alt.Y("area:N", sort="-x", title=None),
            color=alt.condition(alt.datum.surge_ratio >= surge_thr,
                                alt.value("#d62728"), alt.value("#9ecae1")),
            tooltip=["area", alt.Tooltip("surge_ratio:Q", format=".2f"),
                     alt.Tooltip("new_per_day:Q", format=".1f"),
                     alt.Tooltip("new_cases_latest:Q", format=".0f")]
        ).properties(height=380), use_container_width=True)
    st.caption(f"Red = at/above the surge threshold ({surge_thr}×) with "
               f"≥{min_new} new cases in the latest interval.")

    movers = tbl[tbl.flag_surge]["area"].tolist()[:6]
    if movers:
        st.subheader("New-cases-per-day trend for flagged movers")
        src = (bundle["districts"] if level2 == "district" else bundle["provinces"])
        kcol = "district" if level2 == "district" else "area"
        ts = src[src[kcol].isin(movers)].dropna(subset=["new_per_day"])
        st.altair_chart(
            alt.Chart(ts).mark_line(point=True).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("new_per_day:Q", title="New/day"),
                color=alt.Color(f"{kcol}:N", title=None),
                tooltip=["date:T", f"{kcol}:N",
                         alt.Tooltip("new_per_day:Q", format=".1f")]
            ).properties(height=300), use_container_width=True)
    else:
        st.info("No areas currently meet the surge criteria. Lower the surge "
                "ratio or noise floor in the sidebar to widen the net.")

# ---- Burden --------------------------------------------------------------- #
with tab_burden:
    st.subheader("Cumulative case burden")
    level3 = st.radio("Level", ["district", "province"], horizontal=True,
                      key="burden_level")
    tbl = flag_table(level3).sort_values("cum_cases", ascending=False)
    st.altair_chart(
        alt.Chart(tbl).mark_bar().encode(
            x=alt.X("cum_cases:Q", title="Cumulative cases (YTD)"),
            y=alt.Y("area:N", sort="-x", title=None),
            color=alt.condition(alt.datum.flag_burden,
                                alt.value("#ff7f0e"), alt.value("#c6c6c6")),
            tooltip=["area", "cum_cases",
                     alt.Tooltip("share_pct:Q", format=".2f", title="share %")]
        ).properties(height=520), use_container_width=True)
    st.caption(f"Orange = top {burden_n} contributors. Top areas drive the "
               "national caseload and absolute clinical demand.")

# ---- Per-capita ----------------------------------------------------------- #
with tab_pc:
    st.subheader("Per-capita incidence (cases per 100,000)")
    pop = bundle["population"]
    src_year = pop["source_year"].iloc[0] if len(pop) else "?"
    st.caption(f"Population denominator: DCS Census of Population & Housing "
               f"{src_year}. Reporting units CMC, Kalmunai and NIHS are folded "
               "into Colombo, Ampara and Kalutara respectively for district "
               "incidence.")
    level4 = st.radio("Level", ["district", "province"], horizontal=True,
                      key="pc_level")
    metric = st.radio("Metric", ["Cumulative /100k", "Daily /100k (latest)"],
                      horizontal=True)
    col = ("cum_incidence_per100k" if metric.startswith("Cum")
           else "daily_incidence_per100k")
    tbl = flag_table(level4).dropna(subset=[col]).sort_values(col, ascending=False)
    st.altair_chart(
        alt.Chart(tbl).mark_bar().encode(
            x=alt.X(f"{col}:Q", title=metric),
            y=alt.Y("area:N", sort="-x", title=None),
            color=alt.condition(alt.datum.flag_percap,
                                alt.value("#9467bd"), alt.value("#cbb8e0")),
            tooltip=["area", alt.Tooltip(f"{col}:Q", format=",.1f"),
                     "cum_cases", "population"]
        ).properties(height=520), use_container_width=True)
    st.caption("Per-capita normalises for district size — small districts with "
               "high incidence can outrank big-caseload districts.")

# ---- Map ------------------------------------------------------------------ #
with tab_map:
    st.subheader("District choropleth")
    _MAP_LABELS = {
        "cum_incidence_per100k": "Cumulative incidence /100k",
        "cum_cases": "Cumulative cases",
        "new_per_day": "New cases / day",
        "surge_ratio": "Surge ratio"}
    metric_map = st.selectbox(
        "Colour districts by", list(_MAP_LABELS),
        format_func=lambda c: _MAP_LABELS[c])
    dtbl = flag_table("district")

    if GEOJSON.exists():
        import pydeck as pdk
        import matplotlib
        from matplotlib.colors import Normalize

        geo = json.loads(GEOJSON.read_text(encoding="utf-8"))
        vals = dict(zip(dtbl["area"], dtbl[metric_map]))
        series = pd.Series(vals).replace([np.inf, -np.inf], np.nan)
        vmin = float(series.min(skipna=True)) if series.notna().any() else 0.0
        vmax = float(series.max(skipna=True)) if series.notna().any() else 1.0
        norm = Normalize(vmin=vmin, vmax=vmax if vmax > vmin else vmin + 1)
        cmap = matplotlib.colormaps["YlOrRd"]

        for feat in geo["features"]:
            nm = feat["properties"].get("name", "")
            v = vals.get(nm, np.nan)
            feat["properties"]["_name"] = nm
            feat["properties"]["_value"] = (None if pd.isna(v)
                                            else round(float(v), 2))
            if pd.isna(v):
                feat["properties"]["fill_color"] = [220, 220, 220, 160]
            else:
                r, g, bl, _ = cmap(norm(float(v)))
                feat["properties"]["fill_color"] = [int(r * 255), int(g * 255),
                                                    int(bl * 255), 200]

        layer = pdk.Layer(
            "GeoJsonLayer", geo, pickable=True, stroked=True, filled=True,
            get_fill_color="properties.fill_color",
            get_line_color=[255, 255, 255], line_width_min_pixels=0.7)
        view = pdk.ViewState(latitude=7.85, longitude=80.7, zoom=6.4)
        st.pydeck_chart(pdk.Deck(
            layers=[layer], initial_view_state=view, map_style=None,
            tooltip={"text": "{_name}\n" + _MAP_LABELS[metric_map] + ": {_value}"}),
            use_container_width=True)

        # simple gradient legend
        g0, g1 = cmap(0.0), cmap(1.0)
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:8px;font-size:0.85em'>"
            f"<span>{vmin:,.1f}</span>"
            f"<div style='flex:1;height:12px;border-radius:3px;background:linear-gradient("
            f"to right, rgb({int(g0[0]*255)},{int(g0[1]*255)},{int(g0[2]*255)}),"
            f"rgb({int(g1[0]*255)},{int(g1[1]*255)},{int(g1[2]*255)}))'></div>"
            f"<span>{vmax:,.1f}</span>"
            f"<span style='margin-left:8px;color:#888'>{_MAP_LABELS[metric_map]}"
            f" · grey = no data</span></div>", unsafe_allow_html=True)
    else:
        st.warning("District GeoJSON not bundled (lk_districts.geojson missing) "
                   "— showing a ranked bar instead.")
        d = dtbl.dropna(subset=[metric_map]).sort_values(metric_map, ascending=False)
        st.altair_chart(
            alt.Chart(d).mark_bar().encode(
                x=alt.X(f"{metric_map}:Q"),
                y=alt.Y("area:N", sort="-x", title=None),
                tooltip=["area", metric_map]).properties(height=560),
            use_container_width=True)

# ---- Data & export -------------------------------------------------------- #
with tab_data:
    st.subheader("Parsed data & export")
    st.markdown("**National KPIs by date**")
    st.dataframe(nat.drop(columns=[c for c in nat.columns
                                   if c.startswith("month_")], errors="ignore"),
                 use_container_width=True, hide_index=True)

    # Build an Excel workbook for download (fast; rebuilt each run so it always
    # reflects the current data and flag-threshold settings)
    def make_excel():
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xl:
            meta.to_excel(xl, sheet_name="national_meta", index=False)
            nat.to_excel(xl, sheet_name="national_daily", index=False)
            flag_table("district").to_excel(xl, sheet_name="district_flags", index=False)
            flag_table("province").to_excel(xl, sheet_name="province_flags", index=False)
            bundle["districts"].to_excel(xl, sheet_name="district_timeseries", index=False)
            bundle["units"].to_excel(xl, sheet_name="unit_timeseries", index=False)
        return buf.getvalue()

    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇️ District flags (CSV)",
                       flag_table("district").to_csv(index=False).encode(),
                       file_name=f"district_flags_{as_of}.csv", mime="text/csv")
    c2.download_button("⬇️ District time-series (CSV)",
                       bundle["districts"].to_csv(index=False).encode(),
                       file_name=f"district_timeseries_{as_of}.csv", mime="text/csv")
    c3.download_button("⬇️ Full workbook (Excel)", make_excel(),
                       file_name=f"dengue_situational_{as_of}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
