"""
fetch_latest.py — guarded daily data-acquisition agent
======================================================
A fallback for when nobody uploads the daily PDF manually. It checks a
configurable list of **official** sources for a newer NaDSys "Current Status of
Dengue" daily-update PDF, validates it against the *same* parser/QC the
dashboard uses, and — only if every check passes — saves it as the canonical
``Daily Update YYYY. MM. DD.pdf``. Designed to run unattended in GitHub Actions.

Trust model (why this won't poison the dashboard)
-------------------------------------------------
* Downloads ONLY from an allow-list of official domains (``sources.json``).
* A fetched file is accepted only if ALL of these hold:
    - the bytes are a real PDF (``%PDF`` magic),
    - it parses to a snapshot with a date, 25+ district/units and 9 provinces,
    - the district totals AND province totals each equal the national YTD total
      (the integrity check that also validates the manual data),
    - its date is newer than every PDF we already have,
    - its YTD total is not lower than the latest known YTD (monotonic sanity).
  Anything else is rejected and logged — never written into the dashboard data.

Configuration lives in ``sources.json``. Until a real public daily-PDF URL is
known, ``direct_url_templates`` is empty and the agent simply reports
"no new official PDF found" (safe no-op). When you learn the URL pattern, add it
there (placeholders ``{Y}``/``{m}``/``{d}``) and the daily fetch starts working.

Pure standard library + the project's existing parser (no extra dependencies).
Run:  python fetch_latest.py [folder]
"""
from __future__ import annotations

import json
import re
import sys
import ssl
import tempfile
import datetime as dt
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

from dengue_parser import parse_pdf, parse_folder

UA = "DengueDashboardBot/1.0 (+https://github.com/thisarap7/dengue-situational-dashboard)"
MAX_BYTES = 8 * 1024 * 1024          # 8 MB cap per download
TIMEOUT = 30
DEFAULT_CONFIG = {
    "allow_hosts": ["www.epid.gov.lk", "epid.gov.lk",
                    "www.dengue.health.gov.lk", "dengue.health.gov.lk",
                    "dengue.epid.gov.lk"],
    "direct_url_templates": [],
    "scan_pages": [],
    "lookback_days": 5,
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_config(folder: Path) -> dict:
    f = folder / "sources.json"
    cfg = dict(DEFAULT_CONFIG)
    if f.exists():
        try:
            user = json.loads(f.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] could not read sources.json: {e}")
    return cfg


def existing_state(folder: Path):
    """(newest_date, newest_year_total, {dates}) from PDFs already present."""
    snaps = parse_folder(folder)
    if not snaps:
        return None, None, set()
    newest = max(snaps, key=lambda s: s.date)
    dates = {s.date for s in snaps}
    return newest.date, newest.year_total, dates


def _host_ok(url: str, allow_hosts) -> bool:
    return urlparse(url).hostname in set(allow_hosts)


def http_get(url: str) -> bytes | None:
    """Fetch bytes with a UA, timeout, size cap, and TLS verification ON."""
    try:
        req = Request(url, headers={"User-Agent": UA})
        ctx = ssl.create_default_context()           # verify certs (no bypass)
        with urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            print(f"[skip] {url}: exceeds {MAX_BYTES} byte cap")
            return None
        return data
    except (URLError, TimeoutError, ssl.SSLError) as e:
        print(f"[skip] {url}: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[skip] {url}: {e}")
        return None


def candidate_urls(cfg: dict, today: dt.date) -> list[str]:
    """Build the de-duplicated, allow-listed candidate URL list."""
    urls: list[str] = []

    # 1) direct daily-PDF URL templates for the last N days (newest first)
    for tmpl in cfg.get("direct_url_templates", []):
        for back in range(cfg.get("lookback_days", 5) + 1):
            d = today - dt.timedelta(days=back)
            try:
                urls.append(tmpl.format(Y=d.year, m=f"{d.month:02d}",
                                        d=f"{d.day:02d}"))
            except Exception as e:  # noqa: BLE001
                print(f"[warn] bad template {tmpl!r}: {e}")
                break

    # 2) crawl configured official index pages for links to PDFs
    for page in cfg.get("scan_pages", []):
        if not _host_ok(page, cfg["allow_hosts"]):
            print(f"[skip page] {page}: host not in allow-list")
            continue
        html = http_get(page)
        if not html:
            continue
        try:
            text = html.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for href in re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']',
                               text, flags=re.I):
            full = urljoin(page, href)
            if _host_ok(full, cfg["allow_hosts"]):
                urls.append(full)

    # de-dup, preserve order, keep only allow-listed hosts
    seen, out = set(), []
    for u in urls:
        if u not in seen and _host_ok(u, cfg["allow_hosts"]):
            seen.add(u)
            out.append(u)
    return out


def validate(pdf_bytes: bytes, newest_date, newest_ytd):
    """Return (ok: bool, snapshot_or_None, reason: str)."""
    if pdf_bytes[:5] != b"%PDF-":
        return False, None, "not a PDF (bad magic bytes)"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(pdf_bytes)
        tmp = Path(tf.name)
    try:
        snap = parse_pdf(tmp)
    except Exception as e:  # noqa: BLE001
        return False, None, f"unparseable: {e}"
    finally:
        try:
            tmp.unlink()
        except Exception:  # noqa: BLE001
            pass

    if snap.date is None or snap.year_total is None:
        return False, None, "missing date or YTD total"
    if len(snap.districts) < 25 or len(snap.provinces) != 9:
        return False, None, (f"unexpected structure "
                             f"({len(snap.districts)} units, "
                             f"{len(snap.provinces)} provinces)")
    if snap.district_sum() != snap.year_total:
        return False, None, "district totals != national total"
    if snap.province_sum() != snap.year_total:
        return False, None, "province totals != national total"
    if newest_date is not None and snap.date <= newest_date:
        return False, snap, f"not newer than current data ({newest_date})"
    if (newest_ytd is not None and newest_date is not None
            and snap.date.year == newest_date.year
            and snap.year_total < newest_ytd):
        return False, snap, (f"YTD {snap.year_total} < known {newest_ytd} "
                             "(cumulative total should not drop)")
    return True, snap, "ok"


def canonical_name(d: dt.date) -> str:
    return f"Daily Update {d.year}. {d.month:02d}. {d.day:02d}.pdf"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(folder: Path) -> dict:
    cfg = load_config(folder)
    now_utc = dt.datetime.now(dt.timezone.utc)
    today = now_utc.date()
    newest_date, newest_ytd, have = existing_state(folder)
    print(f"Existing data: latest={newest_date} ytd={newest_ytd} "
          f"({len(have)} snapshots)")

    cands = candidate_urls(cfg, today)
    print(f"Checking {len(cands)} candidate URL(s) from official sources…")

    added, rejected = [], []
    for url in cands:
        data = http_get(url)
        if not data:
            continue
        ok, snap, reason = validate(data, newest_date, newest_ytd)
        if ok and snap:
            name = canonical_name(snap.date)
            (folder / name).write_bytes(data)
            added.append(str(snap.date))
            print(f"[ADDED] {name}  <- {url}")
            # update running state so multiple new days are handled in order
            newest_date, newest_ytd = snap.date, snap.year_total
        elif snap is not None and "not newer" not in reason:
            rejected.append({"url": url, "reason": reason})
            print(f"[reject] {url}: {reason}")

    has_sources = bool(cfg.get("direct_url_templates")) or bool(cfg.get("scan_pages"))
    if added:
        result = f"added {len(added)} file(s): {', '.join(sorted(added))}"
    elif not has_sources:
        result = ("no sources configured to auto-fetch the daily PDF "
                  "(add direct_url_templates to sources.json)")
    elif cands:
        result = "no new valid official PDF found among checked sources"
    else:
        result = ("no daily PDF available at the configured public sources yet "
                  "— the daily NaDSys PDF is not currently published publicly; "
                  "add its direct URL to sources.json when known")
    print(f"Result: {result}")

    log = {
        "last_run_utc": now_utc.isoformat(timespec="seconds"),
        "result": result,
        "candidates_checked": len(cands),
        "added": added,
        "rejected": rejected[:10],
        "latest_local_date": str(newest_date) if newest_date else None,
    }
    (folder / "fetch_log.json").write_text(json.dumps(log, indent=2),
                                           encoding="utf-8")
    return log


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    out = run(base)
    # Exit 0 always; signal "new data" via GitHub Actions output if present.
    import os
    gha = os.environ.get("GITHUB_OUTPUT")
    if gha:
        with open(gha, "a", encoding="utf-8") as fh:
            fh.write(f"added={'true' if out['added'] else 'false'}\n")
