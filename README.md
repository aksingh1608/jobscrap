# Daily Internship Job Aggregator (Germany)

Scrapes internship / Werkstudent / HiWi roles from German and global boards,
ranks them against your profile, keeps the best **40–50 per day**, and writes a
dated **Excel sheet** with fit scores, reasons, and clickable apply links.

No dashboard. Free tools only. LinkedIn is **not** scraped — handle that manually.

## Quick start

```bash
cd jobscrap
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1. Edit your profile + filters
nano config.yaml   # paste profile_text, tweak keywords / custom_sites

# 2. Scrape → rank → Excel
python run_pipeline.py

# 3. Open today's sheet
#    exports/internships_YYYY-MM-DD.xlsx
```

## Output (Excel columns)

Fit Score · Title · Company · Location · Source · Language · Posted Date · Fit Reason · Apply Link

Sorted by fit score. Header bold, top row frozen, apply links clickable.

## Pipeline modules

| Stage | Module | What it does |
|-------|--------|--------------|
| config | `config.yaml` | All knobs in one place |
| collect | `src/collect/` | JobSpy, Arbeitsagentur API, Crawl4AI custom sites |
| normalize | `src/normalize.py` | Shared record shape |
| dedup | `src/dedup.py` | RapidFuzz on company + title + location |
| filter | `src/filter.py` | Role, domain, location, freshness, German level |
| rank | `src/rank.py` | Local embeddings + boosts → 0–100 fit score |
| store | `src/store.py` | SQLite history (dedup across days) |
| export | `src/export.py` | Dated Excel file |
| notify | `src/notify.py` | Optional Telegram / SMTP alert |

Logs show drop-off counts at each stage.

## Adding a custom site

Edit `config.yaml` only:

```yaml
sources:
  custom_sites:
    - url: https://example.com/jobs
      name: Example Board
      enabled: true
```

## Scoring

Tune under `scoring:` in `config.yaml`. If the Excel is empty, lower `min_fit_score`
(default 45) or check `data/pipeline.log` for filter drop-off.

## Automation (GitHub Actions)

`.github/workflows/daily_scrape.yml` runs every morning, commits
`data/jobs.db` + `exports/*.xlsx`, and can Telegram/email you the new-job count.

Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional SMTP_*).

## Sources

1. **JobSpy** — Indeed, Glassdoor, Google Jobs (Germany). No LinkedIn.
2. **Arbeitsagentur Jobsuche API**
3. **Custom sites** — plug-in list in config

## Requirements

- Python 3.11+
- First rank run downloads `all-MiniLM-L6-v2` (~80MB)
