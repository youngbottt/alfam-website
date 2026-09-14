# Alfam Insurance Brokers — broker platform & website

A working insurance-broking platform: clients request quotes, the broker emails
NAICOM-licensed insurers, insurers reply through private links, quotes are ranked
(including adequacy against the adjuster's assessed value), and a branded **Alfam**
report is emailed to the client.

## Run it

```bash
pip install -r requirements.txt
python app.py          # http://localhost:8000
```

Demo data (2 requests, 8 insurer requests, 6 quotes) loads automatically on first run.

## Pages

| Route | Page |
|---|---|
| `/` | Home — 50-year badge (founded 1977, golden jubilee 2027), rates, how it works |
| `/about` | About us — history and timeline |
| `/services` | Services & rate guide — NAICOM motor tariff table and all classes |
| `/clients` | Our clients — by sector, with the year each came on board |
| `/claims` | Claim notification |
| `/contact` | Contact us — three offices |
| `/quote` | Request a quote |
| `/privacy` | Privacy & data |
| `/admin` | Broker desk · `/admin/r/<id>` placement file · `/admin/clients` register |
| `/insurer/<token>` | Private insurer response form |

## Rates (2026)

Third-party motor follows the NAICOM-approved tariff: private ₦15,000 (₦3m TPPD),
commercial ₦20,000 (₦5m), truck/general cartage ₦100,000 (₦5m), special types ₦20,000
(₦3m), tricycle ₦5,000 (₦2m), motorcycle ₦3,000 (₦1m). Comprehensive may not be priced
below 5% of the sum insured after rebates or discounts; the market rates 5%–7%.
Other classes use market bands — see `/services`.

43 insurers confirmed by NAICOM under the NIIRA 2025 recapitalisation are preloaded.

## Scoring model

Premium 40 · excess 15 · breadth of cover 15 · claims service record 10 ·
**adequacy against the adjuster's assessed value 20**. Under-insurance is penalised
proportionally, so the cheapest quote does not automatically win.

## Email

Set `ALFAM_SMTP_*` to send for real. Without them, every message is written to
`outbox/` as a readable `.eml` file.

## Google Ads

Banner slots only (no popups). Set `ALFAM_ADSENSE_CLIENT` to switch placeholders for
live AdSense units.

## Deploy

Any host that runs Python (small VPS, Render, Railway, Fly.io). Shared cPanel will
**not** run this. `Procfile` is included for gunicorn.
