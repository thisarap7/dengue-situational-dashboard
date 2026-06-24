# Dengue Situational Dashboard — Sri Lanka

An automated dashboard that turns the **NaDSys "Current Status of Dengue in Sri
Lanka" daily-update PDFs** into a situational-awareness tool: national trends,
area flagging (surging / high-burden / high per-capita), a surge watch, a
district choropleth map, and one-click data export.

Everything is driven by the PDFs you drop into this folder — no manual data
entry.

**Live app:** <https://densit-sl.streamlit.app/>

---

## Quick start

1. Put the daily-update PDFs (e.g. `Daily Update 2026. 06. 23.pdf`) in this
   folder. They're already here.
2. **Double-click `run_dashboard.bat`** (Windows). A browser tab opens at
   <http://localhost:8501>.
   - First time only: if it reports a missing package, run
     `pip install -r requirements.txt` and try again.
3. To add a new day, drop the new PDF in this folder and restart (locally), or
   commit it to the repo (hosted — see *Deploying*). Editors can also upload via
   the password-protected **🔒 Data admin** panel in the sidebar.

Manual launch (any OS):

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## What the dashboard shows

The PDFs report **cumulative year-to-date** numbers. The system derives daily
activity by **differencing consecutive snapshots**, normalised per day (the
updates aren't always exactly one day apart).

| Tab | Contents |
|-----|----------|
| **National trends** | Cumulative cases, derived new-cases/day, monthly totals, and deaths / high-risk MOH areas / midnight inpatients over time. |
| **Area flags** | A ranked table of districts (or provinces / raw reporting units) with flags: 🔴 Surging · 🟠 High burden · 🟣 High per-capita. |
| **Surge watch** | Areas where new cases are accelerating vs. their own recent baseline, plus trend lines for flagged movers. |
| **Burden** | Cumulative case load and share by district / province. |
| **Per-capita** | Cases per 100,000 population (cumulative and latest-daily), normalised for district size. |
| **Map** | District choropleth coloured by incidence / cases / new-per-day / surge ratio. |
| **Data & export** | Parsed tables + CSV and Excel download. |

### Flagging logic (adjustable in the sidebar)

- **🔴 Surging** — latest new-cases/day ≥ *surge ratio* × the area's own recent
  median, **and** ≥ *noise-floor* new cases in the latest interval (so tiny
  districts with 1→3 cases don't trip the flag).
- **🟠 High burden** — the top *N* districts/provinces by cumulative cases.
- **🟣 High per-capita** — districts/provinces in the top *quantile* of
  cumulative incidence per 100k.

---

## How it works (files)

| File | Role |
|------|------|
| `dengue_parser.py` | Deterministic parser: reads each PDF's text layer and extracts the date, KPIs, monthly totals, and the 28-unit + 9-province tables. Validated by checking that units and provinces each sum to the national total. |
| `dengue_analytics.py` | Differencing, surge/acceleration metrics, burden share, per-capita incidence, and the flag table. |
| `app.py` | The Streamlit dashboard. |
| `reference_population.csv` | District & province populations (denominators for per-capita). |
| `lk_districts.geojson` | District boundaries for the map. |
| `run_dashboard.bat` | One-click Windows launcher. |

Quick console summary without the dashboard:

```bash
python dengue_analytics.py .
```

---

## Who can upload (access control)

**Viewing the dashboard is open to everyone. Uploading PDFs is restricted.**

- The **🔒 Data admin** panel in the sidebar reveals the upload control *only*
  after a correct **admin passcode** is entered. The uploader is never created
  for ordinary visitors — it's a server-side gate, not just a hidden widget.
- The passcode is read from `admin_password` in Streamlit **secrets** (never
  committed to the repo). **Until you set it, uploads are disabled for
  everyone** (the safe default) — the dashboard still works, fed by the PDFs in
  the repo.
- The durable way to add data is committing PDFs to the repo, which only people
  with repo write access can do. Sidebar uploads are a session-only convenience
  for trusted editors and vanish when the hosted app restarts.

**Set the passcode (one time):** on Streamlit Cloud open your app → **⋮ →
Settings → Secrets** and add:

```toml
admin_password = "choose-a-strong-passphrase"
```

Save — the app restarts and editors who know the passphrase can upload. To test
admin mode locally, create `.streamlit/secrets.toml` (already git-ignored) with
the same line.

> Want per-person logins instead of one shared passcode? Streamlit supports
> Google/OIDC sign-in (`st.login`); ask and it can be wired in with an email
> allow-list.

---

## Deploying for free (Streamlit Community Cloud)

This repo is deployment-ready (`requirements.txt`, `.streamlit/config.toml`).

1. Push this folder to a **public GitHub repo** (already done if you used the
   automated setup).
2. Go to <https://share.streamlit.io>, sign in with GitHub, and click
   **"Create app" → "Deploy a public app from GitHub"**.
3. Pick this repo, branch `main`, main file `app.py`, choose a subdomain, and
   **Deploy**. First build takes a few minutes.
4. **Set the upload passcode** (see *Who can upload* above) under
   **Settings → Secrets**. Until then, uploads are disabled for everyone.
5. **To update the dashboard with a new day:** commit the new
   `Daily Update ….pdf` to the repo —

   ```bash
   git add "Daily Update 2026. 06. 24.pdf"
   git commit -m "Add 24 June update"
   git push
   ```

   Streamlit Cloud auto-redeploys and the live app refreshes.

> Hosted filesystems are ephemeral: PDFs uploaded via the sidebar live only for
> that session. The durable update path is committing PDFs to the repo (above).

---

## Data sources & notes

- **Cases / deaths / KPIs:** NaDSys surveillance, Epidemiology Unit, Ministry of
  Health, Sri Lanka (the source printed on each PDF).
- **Population denominators:** Department of Census & Statistics, *Census of
  Population and Housing 2024* (final district/province figures). Stored in
  `reference_population.csv` — edit that file to update or swap in mid-year
  estimates.
- **District boundaries:** geoBoundaries (gbOpen) ADM2 for Sri Lanka.
- **Reporting units:** the PDFs report 28 RDHS units = 25 districts + three
  special units (CMC = Colombo MC, Kalmunai, NIHS). For district-level
  per-capita these are folded into Colombo, Ampara and Kalutara respectively.
  The raw 28-unit view is available under "Area flags → unit".

> All daily figures are **derived** by differencing official cumulative totals,
> so a day with a large snapshot gap shows that interval's average per day.
> Occasional small downward revisions in the source data (e.g. a month total
> corrected by −1) can appear as a dip; these are genuine source corrections.
