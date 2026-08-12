# Daily Internship Job Aggregator (Germany)

Scrapes internship / Werkstudent / HiWi roles from German and global boards,
ranks them against your profile, keeps the best **40–50 per day**, and writes a
dated **Excel sheet** with fit scores, reasons, and clickable apply links.

Runs itself every morning at **07:00 Berlin time** via GitHub Actions and
messages you the top matches. No dashboard. Free tools only. LinkedIn is **not**
scraped — handle that manually.

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

Useful flags while tuning:

```bash
python run_pipeline.py --no-notify          # no Telegram/email for this run
python run_pipeline.py --min-score 45       # loosen the bar once
python scripts/smoke_test.py                # offline checks, no network needed

# After tightening config.yaml: re-apply the current rules to jobs already
# stored, so rows admitted under looser settings do not linger until they expire
python scripts/recheck_store.py --dry-run   # show what would be dismissed
python scripts/recheck_store.py             # apply and rewrite today's sheet
```

## Output

Two sheets per workbook:

| Sheet | Contents |
|-------|----------|
| **All Open** | Every job still open and not yet applied/dismissed |
| **New Today** | Only what showed up in today's run |

Columns: New · Fit Score · Title · Company · Location · Source · Language ·
Posted Date · Age (days) · Fit Reason · Apply Link

Sorted by fit score, autofilter on, top row frozen, apply links clickable.
Rows are tinted by score band (≥80 strong, ≥65 good, ≥50 worth a look) and
today's finds are flagged `NEW`.

## Scoring

Fit is a weighted blend of independent signals, each scored 0–1 and rescaled to
0–100:

| Signal | Default weight | What it measures |
|--------|---------------:|------------------|
| `semantic` | 35 | embedding similarity against `profile_text` |
| `role` | 25 | internship / Werkstudent / HiWi match (title beats body) |
| `domain` | 25 | ML / AI / data match (title beats body) |
| `recency` | 8 | how long ago it was posted |
| `language` | 7 | English working language |

Then penalties: `german_penalty` if German above your `german_max_level` is
required, `exclude_penalty` for lingering exclude signals.

Weights are **relative** — doubling all of them changes nothing. Tune under
`scoring:` in `config.yaml`.

Two knobs keep the daily sheet useful:

- `min_fit_score` (55) — the normal bar.
- `min_results` (15) + `floor_fit_score` (50) — if fewer than `min_results`
  clear the bar, the next best are added down to the floor, so a thin day still
  produces a sheet instead of an empty file. Off-domain "intern" roles score
  around 40–45, so the floor sits above them deliberately.

Setting any of these to `0` genuinely means zero — numeric settings are read so
that an explicit `0` is never mistaken for "unset".

If the sheet is still short, check `data/pipeline.log` for the filter drop-off
line, which reports how many jobs each stage rejected and why.

## Pipeline modules

| Stage | Module | What it does |
|-------|--------|--------------|
| config | `config.yaml` | All knobs in one place |
| collect | `src/collect/` | JobSpy, Arbeitsagentur API, Crawl4AI custom sites |
| normalize | `src/normalize.py` | Shared record shape, URL canonicalization |
| dedup | `src/dedup.py` | RapidFuzz on company + title + location |
| filter | `src/filter.py` | Role, domain, location, freshness, seniority, German level |
| rank | `src/rank.py` | Composite 0–100 fit score |
| store | `src/store.py` | SQLite history, expiry, pruning |
| export | `src/export.py` | Dated Excel workbook |
| notify | `src/notify.py` | Telegram / SMTP alert with the top matches |
| matching | `src/textmatch.py` | Word-boundary keyword matching used everywhere |

Keyword matching is **word-boundary based**. Plain substring matching is why
postings like "E-commerce Intern" used to score highly: `"ai" in "e-mail"` is
true. Every keyword check now goes through `src/textmatch.py`.

## Retention

Nothing grows without bound:

```yaml
retention:
  expire_days: 21      # unseen for this long → retired from the sheet
  db_keep_days: 180    # expired rows deleted after this
  keep_exports: 30     # only the newest N .xlsx files are kept
```

Jobs you mark `applied` or `dismissed` in the DB are never resurrected. A job
that reappears on a board after expiring is reopened automatically.

## Adding a custom site

Edit `config.yaml` only:

```yaml
sources:
  custom_sites:
    - url: https://example.com/jobs
      name: Example Board
      enabled: true
```

## Automation (GitHub Actions)

`.github/workflows/daily_scrape.yml` runs every morning at 07:00 Berlin time,
commits `data/jobs.db` + `exports/*.xlsx`, and messages you the top matches.

- **Year-round 07:00.** GitHub cron is UTC-only, so two crons are registered
  (05:00 and 06:00 UTC) and a guard step skips whichever is not 07:00 in Berlin
  that day.
- **Smoke test first** — code breakage fails the run in seconds rather than
  after a 20-minute scrape.
- **Cached model + CPU-only torch** — no 2GB CUDA download, no repeated 80MB
  model fetch.
- **Excel uploaded as a run artifact** (30 days), so the sheet is reachable even
  if the commit step fails.
- **Rebase-and-retry push**, plus a `concurrency` group so two runs never race.
- **Failure alert** — a run that dies tells you, because silence otherwise looks
  identical to "no jobs today".
- **Time budgets and circuit breakers** — a source that fails 3 times in a row
  is dropped for the run, and `collect_budget_s` caps the whole collect stage.
  A dead board costs ~30s of retry backoff per query, which would otherwise eat
  the entire morning slot.

Run it by hand from the Actions tab (`workflow_dispatch`); it accepts a
`min_score` override and a `notify` toggle.

Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional `SMTP_*`,
`NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`).

## Notifications

```yaml
notify:
  telegram: true
  email: false
  top_n: 10              # how many jobs to list in the message
  attach_excel: false    # email only — attach the workbook
  skip_when_empty: false # true = stay silent on days with no new matches
```

The message lists the top matches with scores and clickable links, so you can
triage from your phone without opening the sheet.

## Sources

1. **JobSpy** — Indeed, Glassdoor, Google Jobs (Germany). No LinkedIn.
2. **Arbeitsagentur Jobsuche API**
3. **Custom sites** — plug-in list in config

## Requirements

- Python 3.11+
- First rank run downloads `all-MiniLM-L6-v2` (~80MB). If
  `sentence-transformers` is missing or fails to load, the run continues with
  keyword-only scoring rather than crashing.
