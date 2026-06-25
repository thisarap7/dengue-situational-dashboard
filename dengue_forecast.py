"""
dengue_forecast.py — Tier-1 epidemiological outlook
===================================================
Short-horizon, growth-based situational forecasting that works with the data we
have today:

  * a reconstructed **daily incidence** series (Jan 1 -> latest), using the
    monthly cumulative totals and the precise daily snapshots as anchors so
    there is enough transmission history for the methods below;
  * the **effective reproduction number Rt** (Cori et al. 2013 / EpiEstim), with
    a dengue serial-interval distribution — tells you if transmission is growing
    (Rt > 1) or declining (Rt < 1);
  * **growth rate** and **doubling / halving time**;
  * a **14-day projection** via the renewal equation, with uncertainty fanned
    out from the Rt posterior + Poisson observation noise.

Pure numpy / scipy / pandas — no heavy forecasting dependencies.

All outputs are decision-support, not certainty: they predict *reported* cases,
the series is short, and intervals are deliberately wide.
"""
from __future__ import annotations

import calendar
import numpy as np
import pandas as pd
from scipy import stats

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# --------------------------------------------------------------------------- #
# Serial interval (dengue, human-to-human generation incl. extrinsic period)
# --------------------------------------------------------------------------- #
def serial_interval(mean: float = 17.0, sd: float = 6.0, tmax: int = 40):
    """Discretised gamma serial-interval pmf w[s], s = 0..tmax (w[0] = 0)."""
    k = (mean / sd) ** 2
    theta = sd ** 2 / mean
    g = stats.gamma(a=k, scale=theta)
    s = np.arange(0, tmax + 1)
    w = g.cdf(s + 0.5) - g.cdf(np.maximum(s - 0.5, 0))
    w[0] = 0.0
    return w / w.sum()


# --------------------------------------------------------------------------- #
# Reconstruct a daily national incidence series
# --------------------------------------------------------------------------- #
def reconstruct_national_incidence(meta: pd.DataFrame):
    """Daily incidence (Jan 1 -> latest) built from cumulative anchors:
    month-end cumulative totals for completed months + the exact cumulative
    (year_total) at each daily snapshot. Returns (series, first_snapshot_date).
    """
    m = meta.sort_values("date").copy()
    last = m.iloc[-1]
    year = int(last["year"]) if pd.notna(last.get("year")) else m["date"].max().year
    first_snap = pd.Timestamp(m["date"].min())

    monthly = {}
    for i, name in enumerate(MONTHS, start=1):
        col = f"month_{name}"
        if col in m.columns and pd.notna(last[col]):
            monthly[i] = float(last[col])

    anchors = {pd.Timestamp(year, 1, 1): 0.0}
    cum = 0.0
    for mo in sorted(monthly):
        cum += monthly[mo]
        mend = pd.Timestamp(year, mo, calendar.monthrange(year, mo)[1])
        if mend < first_snap:            # snapshots refine the latest month(s)
            anchors[mend] = cum
        else:
            break
    for _, row in m.iterrows():
        anchors[pd.Timestamp(row["date"])] = float(row["year_total"])

    s = pd.Series(anchors).sort_index()
    daily = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D")
                      ).interpolate("time")
    inc = daily.diff()
    inc.iloc[0] = 0.0
    inc = inc.clip(lower=0)
    inc.name = "incidence"
    return inc, first_snap


def _daily_from_cum(dates, cum):
    """Interpolate an arbitrary (dates, cumulative) series to daily incidence."""
    s = pd.Series(np.asarray(cum, float),
                  index=pd.to_datetime(dates)).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    daily = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D")
                      ).interpolate("time")
    inc = daily.diff()
    inc.iloc[0] = 0.0
    return inc.clip(lower=0)


# --------------------------------------------------------------------------- #
# Rt (Cori instantaneous reproduction number)
# --------------------------------------------------------------------------- #
def estimate_rt(incidence: pd.Series, w, tau: int = 7,
                prior_shape: float = 1.0, prior_scale: float = 5.0,
                min_window_cases: float = 12.0) -> pd.DataFrame:
    I = incidence.values.astype(float)
    n = len(I)
    Lam = np.convolve(I, w)[:n]          # total infectiousness at each day
    rows = []
    for t in range(n):
        lo = t - tau + 1
        if lo < 1:
            continue
        sumI = I[lo:t + 1].sum()
        sumL = Lam[lo:t + 1].sum()
        if sumL <= 0 or sumI < min_window_cases:
            continue
        a = prior_shape + sumI
        scale = 1.0 / (1.0 / prior_scale + sumL)
        rows.append((incidence.index[t], a * scale,
                     stats.gamma.ppf(0.025, a, scale=scale),
                     stats.gamma.ppf(0.975, a, scale=scale), a, scale))
    return pd.DataFrame(rows, columns=["date", "rt", "rt_lo", "rt_hi",
                                       "post_shape", "post_scale"])


# --------------------------------------------------------------------------- #
# Growth rate & doubling time
# --------------------------------------------------------------------------- #
def latest_growth(incidence: pd.Series, window: int = 10) -> dict:
    tail = incidence.values[-window:].astype(float)
    x = np.arange(len(tail))
    y = np.log(np.maximum(tail, 0.5))
    if len(tail) < 3:
        return dict(r=np.nan, r_se=np.nan, doubling=None, halving=None)
    fit, cov = np.polyfit(x, y, 1, cov=True)
    r, se = float(fit[0]), float(np.sqrt(cov[0, 0]))
    return dict(r=r, r_se=se,
                doubling=(np.log(2) / r if r > 0 else None),
                halving=(np.log(2) / abs(r) if r < 0 else None))


# --------------------------------------------------------------------------- #
# 14-day projection (renewal equation, Monte Carlo)
# --------------------------------------------------------------------------- #
def project_renewal(incidence: pd.Series, w, post_shape: float,
                    post_scale: float, horizon: int = 14,
                    nsim: int = 2000, seed: int = 0,
                    nb_size: float = 25.0) -> pd.DataFrame:
    """Renewal-equation Monte-Carlo projection. Uncertainty = Rt-posterior
    draws + a negative-binomial observation model (overdispersion nb_size; lower
    = wider) so the fan reflects realistic day-to-day variability, not just
    Poisson noise."""
    rng = np.random.default_rng(seed)
    I = incidence.values.astype(float)
    S = len(w) - 1
    rt_draws = stats.gamma.rvs(post_shape, scale=post_scale, size=nsim,
                               random_state=rng)
    wr = w[1:S + 1][::-1]                 # aligns with most-recent-first history
    sims = np.zeros((nsim, horizon))
    base = list(I[-S:]) if len(I) >= S else [0.0] * (S - len(I)) + list(I)
    for j in range(nsim):
        hist = list(base)
        for h in range(horizon):
            recent = np.array(hist[-S:])
            lam = float(np.sum(wr * recent))
            mu = rt_draws[j] * lam
            if mu > 0:
                draw = rng.negative_binomial(nb_size, nb_size / (nb_size + mu))
            else:
                draw = 0
            sims[j, h] = draw
            hist.append(draw)
    qs = np.percentile(sims, [5, 25, 50, 75, 95], axis=0)
    idx = pd.date_range(incidence.index[-1] + pd.Timedelta(days=1),
                        periods=horizon)
    out = pd.DataFrame({"date": idx, "p05": qs[0], "p25": qs[1],
                        "p50": qs[2], "p75": qs[3], "p95": qs[4]})
    out["cum_p50"] = out["p50"].cumsum()
    out["cum_p05"] = out["p05"].cumsum()
    out["cum_p95"] = out["p95"].cumsum()
    return out


# --------------------------------------------------------------------------- #
# Convenience bundles
# --------------------------------------------------------------------------- #
def national_outlook(meta: pd.DataFrame, si_mean=17.0, si_sd=6.0,
                     tau=7, horizon=14) -> dict:
    inc, first_snap = reconstruct_national_incidence(meta)
    w = serial_interval(si_mean, si_sd)
    rt = estimate_rt(inc, w, tau=tau)
    growth = latest_growth(inc, window=min(14, len(inc)))
    proj = None
    rt_now = None
    if not rt.empty:
        last = rt.iloc[-1]
        rt_now = dict(rt=last["rt"], lo=last["rt_lo"], hi=last["rt_hi"],
                      date=last["date"])
        proj = project_renewal(inc, w, last["post_shape"], last["post_scale"],
                               horizon=horizon)
    return dict(incidence=inc, first_snap=first_snap, rt=rt, rt_now=rt_now,
                growth=growth, projection=proj, horizon=horizon,
                last_cum=float(meta.sort_values("date").iloc[-1]["year_total"]))


def district_outlook(districts_df: pd.DataFrame, horizon: int = 14,
                     window: int = 10, top: int = 12) -> pd.DataFrame:
    """Short-series 14-day growth outlook per district (exponential trend)."""
    latest = districts_df.sort_values("date").groupby("district")["cum_cases"].last()
    focus = latest.sort_values(ascending=False).head(top).index
    rows = []
    for d in focus:
        g = districts_df[districts_df.district == d].sort_values("date")
        if g["date"].nunique() < 3:
            continue
        inc = _daily_from_cum(g["date"], g["cum_cases"])
        gr = latest_growth(inc, window=min(window, len(inc)))
        recent = float(inc.values[-min(window, len(inc)):].mean())
        r = gr["r"]
        if r is None or np.isnan(r):
            proj14 = np.nan
        else:
            proj14 = float(np.sum(recent * np.exp(r * np.arange(1, horizon + 1))))
        rows.append({
            "district": d,
            "recent_per_day": recent,
            "growth_pct_day": 100 * r if r is not None and not np.isnan(r) else np.nan,
            "doubling_days": gr["doubling"],
            "halving_days": gr["halving"],
            f"proj_new_{horizon}d": proj14,
            "trend": ("rising" if (r or 0) > 0.01 else
                      "falling" if (r or 0) < -0.01 else "flat"),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    from dengue_analytics import build_all
    b = build_all(sys.argv[1] if len(sys.argv) > 1 else ".")
    out = national_outlook(b["meta"])
    inc = out["incidence"]
    print(f"Reconstructed daily incidence: {len(inc)} days "
          f"({inc.index.min().date()} -> {inc.index.max().date()}), "
          f"latest ~{inc.values[-1]:.0f}/day")
    if out["rt_now"]:
        r = out["rt_now"]
        print(f"Current Rt = {r['rt']:.2f}  (95% CrI {r['lo']:.2f}-{r['hi']:.2f}) "
              f"as of {pd.Timestamp(r['date']).date()}")
    g = out["growth"]
    if g["doubling"]:
        print(f"Growth {100*g['r']:.1f}%/day, doubling ~{g['doubling']:.1f} days")
    elif g["halving"]:
        print(f"Decline {100*g['r']:.1f}%/day, halving ~{g['halving']:.1f} days")
    if out["projection"] is not None:
        p = out["projection"]
        tot = p["p50"].sum()
        print(f"Projected next {out['horizon']} days: ~{tot:,.0f} new cases "
              f"(90% range {p['p05'].sum():,.0f}-{p['p95'].sum():,.0f})")
        print(f"Projected cumulative by {p['date'].iloc[-1].date()}: "
              f"~{out['last_cum'] + p['cum_p50'].iloc[-1]:,.0f}")
