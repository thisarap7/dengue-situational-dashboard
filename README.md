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

**Viewing the dashboard is open to everyone. Uploading PDFs is restricted to
named Google accounts.**

- The **🔒 Data admin** panel offers **Sign in with Google** (Streamlit's native
  OIDC login). After sign-in, the upload control appears **only** if your email
  is on the `editor_emails` allow-list. The uploader is never created for anyone
  else — a server-side gate, not just a hidden widget.
- **Until Google sign-in is configured, uploads are disabled for everyone** (the
  safe default); the dashboard still runs on the PDFs in the repo.
- The durable way to add data is committing PDFs to the repo (repo write access
  required). Sidebar uploads are a session-only convenience and vanish when the
  hosted app restarts.

### One-time setup

**1. Create a Google OAuth client** (Google Cloud Console → *APIs & Services*):

- *OAuth consent screen* → **External** → add app name + your email. Scopes
  `openid`, `email`, `profile` are non-sensitive (no Google verification
  needed). Publish to **Production** (or keep **Testing** and add each editor as
  a "test user" for an extra layer).
- *Credentials* → **Create credentials → OAuth client ID → Web application**.
  Under **Authorized redirect URIs** add (exact match matters):
  - `https://densit-sl.streamlit.app/oauth2callback`  *(deployed)*
  - `http://localhost:8501/oauth2callback`  *(optional, for local testing)*
- Copy the **Client ID** and **Client secret**.

**2. Add secrets** on Streamlit Cloud (your app → **⋮ → Settings → Secrets**):

```toml
# Who may upload (lower-case Google emails)
editor_emails = ["thisaraperera7@gmail.com", "colleague@example.com"]

[auth]
redirect_uri = "https://densit-sl.streamlit.app/oauth2callback"
cookie_secret = "a-long-random-string-change-me"   # e.g. python -c "import secrets;print(secrets.token_hex(32))"
client_id = "<google-client-id>"
client_secret = "<google-client-secret>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Save — the app restarts. Allow-listed editors can now sign in and upload;
everyone else keeps view-only access. To add/remove an editor later, just edit
`editor_emails` (no redeploy needed).

**Local testing:** put the same secrets in `.streamlit/secrets.toml` (already
git-ignored) but with `redirect_uri = "http://localhost:8501/oauth2callback"`,
and `pip install -r requirements.txt` (installs `Authlib`).

---

## Deploying for free (Streamlit Community Cloud)

This repo is deployment-ready (`requirements.txt`, `.streamlit/config.toml`).

1. Push this folder to a **public GitHub repo** (already done if you used the
   automated setup).
2. Go to <https://share.streamlit.io>, sign in with GitHub, and click
   **"Create app" → "Deploy a public app from GitHub"**.
3. Pick this repo, branch `main`, main file `app.py`, choose a subdomain, and
   **Deploy**. First build takes a few minutes.
4. **Enable editor uploads** (optional) by configuring Google sign-in under
   **Settings → Secrets** — see *Who can upload* above. Until then, uploads are
   disabled for everyone (the dashboard still works on the repo's PDFs).
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

## Automated daily updates (the auto-fetch agent)

If nobody uploads the daily PDF, a **GitHub Action** (`.github/workflows/
daily-dengue-fetch.yml`) runs `fetch_latest.py` on a schedule as a safety net.
The agent:

1. Checks the **official sources** listed in `sources.json` for a newer
   "Current Status of Dengue" PDF (only hosts on its allow-list are fetched).
2. **Validates** each candidate with the same parser/QC the dashboard uses —
   real PDF, parses to a snapshot, 25+ units and 9 provinces, district **and**
   province totals equal the national YTD, date newer than what we have, and
   YTD not lower than the last known value.
3. Only if every check passes, commits the new `Daily Update ….pdf` (which
   auto-redeploys the app). Everything it does is written to `fetch_log.json`,
   surfaced in the dashboard header ("🤖 Auto-fetch agent last ran …").

The dashboard header also shows a **freshness badge** (🟢 up to date / 🟡 a few
days old / 🔴 please update).

### Important: the daily PDF isn't on a public URL (yet)

The daily NaDSys infographic is produced by the **login-gated** NaDSys portal
(`dengue.epid.gov.lk`); it is **not** currently published at a public, scrapable
URL. So the agent is **wired and ready but will report "no new PDF" until you
point it at a real source.** When you find the URL the daily PDF lives at, add
its pattern to `sources.json`:

```jsonc
"direct_url_templates": [
  "https://<official-host>/path/Daily%20Update%20{Y}.%20{m}.%20{d}.pdf"
],
// {Y}=year, {m}=2-digit month, {d}=2-digit day; the agent tries the last few days.
```

Only hosts in `allow_hosts` are ever fetched, and the validation gate still
applies — so a wrong or malicious URL can't push bad data into the dashboard.

### Running / triggering it

- **Automatic:** twice daily (see the `cron` lines in the workflow; times are
  UTC — Sri Lanka is UTC+5:30).
- **Manual:** repo → **Actions → "Daily dengue auto-fetch" → Run workflow**.
- **One-time setup:** the Action commits to the repo, so enable write access at
  repo → **Settings → Actions → General → Workflow permissions → "Read and
  write permissions"**.

---

## Auto-push new PDFs from your PC (instant sync)

So you never have to run git by hand: a small **folder watcher** auto-commits
and pushes any new `Daily Update ….pdf` the moment you drop it into this folder
(which then auto-redeploys the live app).

- **Install once:** right-click `install_autosync.ps1` → *Run with PowerShell*
  (no admin needed). It drops a hidden launcher in your Startup folder and
  starts the watcher — so it runs automatically at **every login**.
- **What it does on a new PDF:** waits a few seconds for the copy to finish →
  stages **only `*.pdf`** (never your posters/screenshots/docs) → commits →
  `git pull --rebase` (so it never clashes with the auto-fetch bot) → `git push`.
- **Manual sync anytime:** double-click `sync_now.bat`.
- **Activity log:** `auto_sync.log` (git-ignored).
- **Turn it off:** delete `DengueAutoSync.vbs` from your Startup folder
  (`Win+R` → `shell:startup`).

Scripts: `watch_and_push.ps1` (the watcher), `install_autosync.ps1` (sets up
auto-start), `sync_now.bat` (one-click manual push).

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
