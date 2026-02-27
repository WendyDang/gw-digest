# 🌌 GW Daily Digest

An AI-powered daily email digest of gravitational wave papers from arXiv, filtered and summarized by Claude.

**Every weekday morning**, this tool:
1. Fetches new papers from `gr-qc`, `astro-ph.CO`, and `astro-ph.HE`
2. Asks Claude to score each paper's relevance (1–10) to your research interests
3. Summarizes the relevant ones with structured technical summaries
4. Sends a beautifully formatted HTML email to your inbox

---

## 📬 Example Output

Each paper in the digest gets:
- **Relevance score** (7–10) with color-coded priority
- **Tags** (dark_sirens, parameter_estimation, tests_of_GR, LIGO, ...)
- **Read priority** (Must-read 🔴 / Read 🟡 / Skim ⚪)
- **Structured summary**: TL;DR · What they did · Key result · Why it matters · Novelty
- Direct links to Abstract, PDF, and ar5iv HTML version

---

## 🚀 Quick Start

### 1. Fork this repository
Click **Fork** on GitHub to create your own copy.

### 2. Set up Gmail App Password
1. Enable 2-factor authentication on your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create an app password for "Mail" → copy the 16-character password

### 3. Add GitHub Secrets
Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key from [console.anthropic.com](https://console.anthropic.com) |
| `GMAIL_USER` | The Gmail address sending the email (e.g. `mybot@gmail.com`) |
| `GMAIL_APP_PASSWORD` | The 16-character app password from step 2 |
| `YOUR_EMAIL` | The address to receive the digest (can be the same or different) |

### 4. Enable GitHub Actions
Go to the **Actions** tab in your repo and click **"I understand my workflows, go ahead and enable them"**.

### 5. Test it manually
Go to **Actions** → **GW Daily Digest** → **Run workflow** → set **Dry run = true** → Run.
Download the HTML artifact to preview the digest before it goes to your inbox.

---

## ⚙️ Configuration

### Customize your research interests
Edit **`config/interests.txt`** to describe exactly what you care about.
Claude reads this file when scoring papers — the more precise, the better the filtering.

### Environment variables (all optional)

| Variable | Default | Description |
|---|---|---|
| `ARXIV_CATEGORIES` | `gr-qc,astro-ph.CO,astro-ph.HE` | Comma-separated arXiv categories |
| `RELEVANCE_THRESHOLD` | `7` | Minimum score (1–10) to include a paper |
| `MAX_PAPERS` | `100` | Max papers to screen per run |

Set these as additional GitHub Secrets or directly in the workflow YAML.

### Change the schedule
Edit `.github/workflows/gw_digest.yml`:
```yaml
- cron: '0 9 * * 1-5'   # 9:00 AM UTC, Mon–Fri
```
Use [crontab.guru](https://crontab.guru) to find the right cron expression for your timezone.

---

## 🏃 Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ANTHROPIC_API_KEY="sk-ant-..."
export GMAIL_USER="sender@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export YOUR_EMAIL="you@example.com"

# Dry run (saves HTML preview, no email sent)
DRY_RUN=true python gw_digest.py

# Open the preview
open /tmp/gw_digest_preview.html

# Full run (sends email)
python gw_digest.py
```

---

## 💰 Cost Estimate

Each paper requires 2 Claude API calls (scoring + summarization).

| Papers screened/day | Relevant papers | Estimated cost/day |
|---|---|---|
| 80 | ~8–12 | ~$0.08–0.15 |
| 100 | ~10–15 | ~$0.10–0.20 |

Using Claude Sonnet. Monthly cost: **~$2–5**.

---

## 🔧 Tuning Tips

- **Too many papers?** Raise `RELEVANCE_THRESHOLD` to 8
- **Missing papers?** Lower threshold to 6, or add keywords to `config/interests.txt`
- **Wrong category?** Add/remove entries from `ARXIV_CATEGORIES`
- **Slow?** Reduce `MAX_PAPERS`

---

## 📁 Repository Structure

```
gw-digest/
├── gw_digest.py              # Main script
├── requirements.txt          # Python dependencies
├── config/
│   └── interests.txt         # Your custom research interest profile
└── .github/
    └── workflows/
        └── gw_digest.yml     # GitHub Actions schedule
```

---

## 🤝 Contributing

PRs welcome! Ideas:
- Slack / Discord notification support
- Author watchlist (always include papers from specific researchers)
- Weekly recap mode
- Citation count filtering via Semantic Scholar API

---

## 📄 License

MIT
