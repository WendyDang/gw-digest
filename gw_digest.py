"""
GW Daily Digest — Main Script
Fetches recent arXiv papers, scores them with Claude, and emails a digest.
"""

import arxiv
import anthropic
import smtplib
import json
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, timedelta, timezone
from time import sleep

# ─────────────────────────────────────────────
# CONFIG (override via environment variables)
# ─────────────────────────────────────────────
CATEGORIES       = os.getenv("ARXIV_CATEGORIES", "gr-qc,astro-ph.CO,astro-ph.HE").split(",")
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", "7"))
MAX_PAPERS       = int(os.getenv("MAX_PAPERS", "100"))
YOUR_EMAIL       = os.getenv("YOUR_EMAIL", "you@example.com")
GMAIL_USER       = os.getenv("GMAIL_USER", "sender@gmail.com")
GMAIL_APP_PASS   = os.getenv("GMAIL_APP_PASSWORD", "")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
DRY_RUN          = os.getenv("DRY_RUN", "false").lower() == "true"

# Load custom interest profile if present
CUSTOM_INTERESTS_FILE = os.path.join(os.path.dirname(__file__), "config", "interests.txt")
if os.path.exists(CUSTOM_INTERESTS_FILE):
    with open(CUSTOM_INTERESTS_FILE) as f:
        CUSTOM_INTERESTS = f.read().strip()
else:
    CUSTOM_INTERESTS = ""

# ─────────────────────────────────────────────
# INTEREST PROFILE (edit this or use config/interests.txt)
# ─────────────────────────────────────────────
DEFAULT_INTEREST_PROFILE = """
1. Dark sirens / bright sirens / standard sirens:
   - GW standard sirens for Hubble constant (H0) measurements
   - Galaxy catalog methods, dark siren statistics
   - EM counterpart follow-up, multi-messenger cosmology

2. Gravitational wave parameter estimation:
   - Bayesian inference, MCMC, nested sampling
   - PE pipelines: LALInference, Bilby, RIFT, cogwheel
   - Fisher matrix forecasts, waveform systematics
   - Source characterization (masses, spins, sky localization)

3. Tests of General Relativity with GWs:
   - Modified gravity theories, GW speed constraints
   - Polarization modes beyond GR (scalar, vector)
   - Post-Newtonian / post-Minkowskian deviations
   - Black hole spectroscopy, ringdown, no-hair theorem

4. Major LIGO/Virgo/KAGRA results:
   - New GW event detections or catalog updates (GWTC-x)
   - Instrumental noise, calibration, sensitivity improvements
   - Search pipeline papers (matched filter, unmodeled)

5. Highly novel ideas targeting PRL-level impact:
   - First-of-kind methods or observations
   - Unexpected or surprising results
   - Cross-cutting ideas connecting GW physics to other fields
"""

INTEREST_PROFILE = CUSTOM_INTERESTS if CUSTOM_INTERESTS else DEFAULT_INTEREST_PROFILE

# Tag color palette
TAG_COLORS = {
    "dark_sirens":          "#6c3483",
    "bright_sirens":        "#6c3483",
    "standard_sirens":      "#6c3483",
    "H0":                   "#6c3483",
    "cosmology":            "#1f618d",
    "parameter_estimation": "#1a5276",
    "Bayesian":             "#1a5276",
    "Fisher_matrix":        "#1a5276",
    "waveform":             "#1a5276",
    "tests_of_GR":          "#145a32",
    "modified_gravity":     "#145a32",
    "polarization":         "#145a32",
    "ringdown":             "#145a32",
    "LIGO":                 "#784212",
    "Virgo":                "#784212",
    "KAGRA":                "#784212",
    "detection":            "#784212",
    "novel":                "#7b241c",
    "PRL":                  "#7b241c",
}
DEFAULT_TAG_COLOR = "#555555"

# Cache file to persist high-scoring papers across runs
CACHE_FILE = os.path.join(os.path.dirname(__file__), "config", "top_papers_cache.json")
CACHE_MIN_SCORE = 9    # Only cache papers scoring 9 or 10
FALLBACK_COUNT  = 2    # How many past papers to show when today has no 9/10s


# ─────────────────────────────────────────────
# PAPER CACHE (persists top papers across days)
# ─────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_to_cache(papers_with_scores):
    cache = load_cache()
    existing_ids = {p["arxiv_id"] for p in cache}
    for paper, score_info, summary in papers_with_scores:
        if score_info.get("score", 0) >= CACHE_MIN_SCORE:
            arxiv_id = paper.entry_id.split("/")[-1]
            if arxiv_id not in existing_ids:
                cache.append({
                    "arxiv_id":   arxiv_id,
                    "title":      paper.title,
                    "authors":    [a.name for a in paper.authors[:6]],
                    "entry_id":   paper.entry_id,
                    "date":       str(paper.published.date()),
                    "abstract":   paper.summary,
                    "score_info": score_info,
                    "ai_summary": summary,
                })
                existing_ids.add(arxiv_id)
    # Keep most recent 60
    cache = sorted(cache, key=lambda x: x["date"], reverse=True)[:60]
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"  Cache updated: {len(cache)} top papers stored")


def get_fallback_papers(exclude_ids):
    cache = load_cache()
    fallbacks = []
    for entry in cache:
        if entry["arxiv_id"] not in exclude_ids:
            fallbacks.append(entry)
        if len(fallbacks) >= FALLBACK_COUNT:
            break
    return fallbacks


# ─────────────────────────────────────────────
# PAPER FETCHING
# ─────────────────────────────────────────────
def fetch_recent_papers():
    """Fetch papers submitted in the last 24–36 hours across target categories."""
    papers = []
    seen_ids = set()
    # Look back further on Mondays (covers Thu–Mon) vs Thursdays (covers Mon–Thu)
    weekday = date.today().weekday()  # 0=Mon, 3=Thu
    if weekday == 0:   # Monday
        lookback_days = 4  # back to Thursday
    elif weekday == 3:  # Thursday
        lookback_days = 3  # back to Monday
    else:
        lookback_days = 1  # fallback
    cutoff = date.today() - timedelta(days=lookback_days)

    for cat in CATEGORIES:
        print(f"  Fetching category: {cat}")
        search = arxiv.Search(
            query=f"cat:{cat}",
            max_results=MAX_PAPERS // len(CATEGORIES) + 10,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        client = arxiv.Client()
        for p in client.results(search):
            pub_date = p.published.replace(tzinfo=timezone.utc).date()
            if pub_date < cutoff:
                break
            if p.entry_id not in seen_ids:
                seen_ids.add(p.entry_id)
                papers.append(p)

    print(f"  → {len(papers)} unique papers after dedup")
    return papers[:MAX_PAPERS]


# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────
KEYWORD_FILTER = [
    "gravitational wave", "gravitational-wave", "gw event", "ligo", "virgo", "kagra",
    "standard siren", "dark siren", "bright siren", "binary neutron star", "neutron star merger",
    "black hole merger", "compact binary", "binary black hole", "bbh", "bns", "nsbh",
    "parameter estimation", "bayesian inference", "ringdown", "quasi-normal mode",
    "hubble constant", "h0 measurement", "waveform model", "matched filter",
    "post-newtonian", "effective-one-body", "eob model", "imr", "nr surrogate",
    "gwtc", "o3 catalog", "o4", "einstein telescope", "cosmic explorer", "lisa",
    "tests of gr", "modified gravity", "scalar-tensor", "lorentz violation",
    "polarization mode", "extra polarization", "tidal deformability",
    "neutron star equation of state", "kilonova", "multi-messenger",
]

def passes_keyword_filter(paper):
    """Return True if the paper likely warrants Claude scoring."""
    text = (paper.title + " " + paper.summary).lower()
    return any(kw in text for kw in KEYWORD_FILTER)


def score_paper(claude_client, paper):
    """Ask Claude to score paper relevance 1–10 and return structured JSON."""
    authors_str = ", ".join(a.name for a in paper.authors[:6])
    if len(paper.authors) > 6:
        authors_str += " et al."

    prompt = f"""You are an expert gravitational wave physicist and cosmologist with very high standards.
Score this paper's relevance (1–10) to these SPECIFIC research interests:

{INTEREST_PROFILE}

STRICT scoring rules — be conservative, most papers should score low:
- 9–10: DIRECTLY on one of the core topics above. Must be about dark/bright/standard sirens, 
        GW parameter estimation methods, tests of GR with GWs, or a major LIGO/Virgo/KAGRA result.
        Score 10 only for truly landmark results (new detection, major H0 measurement, etc.)
- 7–8:  Clearly relevant and useful — touches the specific topics above, not just loosely related GW work
- 5–6:  GW-adjacent but NOT on the specific topics (e.g. generic BH mergers, generic cosmology without sirens)
- 3–4:  General relativity or astrophysics with minor GW connection
- 1–2:  Not relevant at all (galaxy surveys, CMB without GW, pure EM astronomy, etc.)

IMPORTANT: A paper about generic compact binaries, gravitational waveforms for unrelated purposes,
or general cosmology WITHOUT the specific siren/PE/GR-test connection should score 5 or below.
Be strict — only score 7+ if you would genuinely recommend a GW cosmologist read this paper.

Paper details:
Title: {paper.title}
Authors: {authors_str}
Categories: {', '.join(paper.categories)}
Abstract: {paper.summary[:1500]}

Return ONLY a valid JSON object, no other text:
{{
  "score": <integer 1-10>,
  "reason": "<one sentence explaining the score>",
  "tags": ["<tag1>", "<tag2>"],
  "novelty": "<low|medium|high>",
  "suggested_read_priority": "<skim|read|must-read>"
}}"""

    try:
        response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"    [WARN] Score parse error: {e}")
        return {"score": 0, "reason": "parse error", "tags": [], "novelty": "unknown", "suggested_read_priority": "skim"}


# ─────────────────────────────────────────────
# SUMMARIZATION
# ─────────────────────────────────────────────
def summarize_paper(claude_client, paper, score_info):
    """Generate a structured technical summary of the paper."""
    prompt = f"""You are an expert gravitational wave physicist summarizing a paper for a colleague.

Title: {paper.title}
Abstract: {paper.summary}

Write a concise structured summary using these exact headers (use **bold** for headers):

**TL;DR:** One sentence capturing the core contribution.
**What they did:** 2–3 sentences describing the method or analysis.
**Key result:** The most important quantitative or qualitative finding.
**Why it matters:** Relevance to GW cosmology, parameter estimation, or GR tests.
**Novelty:** What makes this new compared to prior work.

Be technical but concise. Total length: under 220 words."""

    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=450,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"    [WARN] Summary error: {e}")
        return "Summary unavailable."


# ─────────────────────────────────────────────
# EMAIL GENERATION
# ─────────────────────────────────────────────
PRIORITY_BADGE = {
    "must-read": ("🔴 Must-read", "#e74c3c"),
    "read":      ("🟡 Read",      "#e67e22"),
    "skim":      ("⚪ Skim",      "#95a5a6"),
}

NOVELTY_BADGE = {
    "high":   "✨ High novelty",
    "medium": "Novel",
    "low":    "",
}

def render_paper_card(title, authors_str, score, score_info, summary, entry_id, is_fallback=False, fallback_date=None):
    """Render a single paper card as HTML."""
    border_color = "#27ae60" if score >= 9 else "#e67e22" if score >= 7 else "#aaa"
    if is_fallback:
        border_color = "#5d6d7e"  # grey-blue for archive papers

    tags_html = " ".join(
        f'<span style="background:{TAG_COLORS.get(t, DEFAULT_TAG_COLOR)};color:white;'
        f'padding:2px 9px;border-radius:12px;font-size:11px;margin:2px;display:inline-block">{t}</span>'
        for t in score_info.get("tags", [])
    )
    priority_label, priority_color = PRIORITY_BADGE.get(
        score_info.get("suggested_read_priority", "skim"), ("⚪ Skim", "#95a5a6")
    )
    novelty_str = NOVELTY_BADGE.get(score_info.get("novelty", ""), "")
    arxiv_id  = entry_id.split("/")[-1]
    abs_url   = entry_id
    pdf_url   = entry_id.replace("abs", "pdf")
    html5_url = f"https://ar5iv.org/abs/{arxiv_id}"

    summary_html = summary.replace("**", "<strong>", 1)
    while "**" in summary_html:
        summary_html = summary_html.replace("**", "</strong>", 1)
    summary_html = summary_html.replace("\n", "<br>")

    date_str = f'&nbsp;|&nbsp;<em>Originally scored {fallback_date}</em>' if is_fallback and fallback_date else ""

    return f"""
<div style="border-left:4px solid {border_color};padding:16px 18px;margin:20px 0;
            background:{'#f0f4f8' if is_fallback else '#fafafa'};border-radius:6px;
            box-shadow:0 1px 3px rgba(0,0,0,0.07)">
  <h3 style="margin:0 0 6px 0;font-size:15px;line-height:1.4;color:#1a1a2e">{title}</h3>
  <p style="color:#666;margin:0 0 8px 0;font-size:12px">
    {authors_str}&nbsp;|&nbsp;
    <strong style="color:{border_color}">{score}/10</strong>&nbsp;|&nbsp;
    <span style="color:{priority_color}">{priority_label}</span>
    {"&nbsp;|&nbsp;<em>" + novelty_str + "</em>" if novelty_str else ""}
    {date_str}
  </p>
  <div style="margin-bottom:10px">{tags_html}</div>
  <div style="font-size:13.5px;line-height:1.65;color:#333">{summary_html}</div>
  <p style="margin-top:12px;font-size:12px">
    <a href="{abs_url}" style="color:#2980b9;text-decoration:none">📄 Abstract</a>&nbsp;&nbsp;
    <a href="{pdf_url}" style="color:#2980b9;text-decoration:none">📥 PDF</a>&nbsp;&nbsp;
    <a href="{html5_url}" style="color:#2980b9;text-decoration:none">🖥️ HTML</a>&nbsp;&nbsp;
    <span style="color:#999">{arxiv_id}</span>
  </p>
</div>"""


def build_html(relevant_papers, total_screened, fallback_entries=None):
    today = date.today().strftime("%B %d, %Y")
    fallback_entries = fallback_entries or []
    must_reads = sum(1 for _, s, _ in relevant_papers if s.get("suggested_read_priority") == "must-read")

    # Today's papers
    sections = []
    for paper, score_info, summary in relevant_papers:
        score = score_info.get("score", 0)
        authors_str = ", ".join(a.name for a in paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += " et al."
        sections.append(render_paper_card(
            title=paper.title, authors_str=authors_str, score=score,
            score_info=score_info, summary=summary, entry_id=paper.entry_id
        ))

    papers_html = "\n".join(sections)

    # Fallback section (past high-scoring papers)
    fallback_html = ""
    if fallback_entries:
        fallback_cards = []
        for entry in fallback_entries:
            authors_str = ", ".join(entry["authors"][:5])
            if len(entry["authors"]) > 5:
                authors_str += " et al."
            fallback_cards.append(render_paper_card(
                title=entry["title"], authors_str=authors_str,
                score=entry["score_info"].get("score", 9),
                score_info=entry["score_info"], summary=entry["ai_summary"],
                entry_id=entry["entry_id"], is_fallback=True,
                fallback_date=entry.get("date", "")
            ))
        fallback_html = f"""
<div style="margin-top:36px">
  <h2 style="color:#5d6d7e;font-size:16px;border-bottom:2px solid #d5dbdb;padding-bottom:6px">
    📚 From the Archive — Top Papers You May Have Missed
  </h2>
  <p style="color:#888;font-size:12px;margin-top:4px">
    No papers scored 9–10 today. Here are recent high-scoring papers from your digest history.
  </p>
  {"".join(fallback_cards)}
</div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;max-width:820px;margin:auto;padding:20px;color:#222">

  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;
              padding:28px 32px;border-radius:10px;margin-bottom:24px">
    <h1 style="margin:0 0 8px 0;font-size:22px">🌌 Gravitational Wave Daily Digest</h1>
    <p style="margin:0;opacity:0.8;font-size:14px">{today}</p>
    <p style="margin:8px 0 0 0;font-size:13px;opacity:0.7">
      Screened <strong>{total_screened}</strong> papers &nbsp;·&nbsp;
      <strong>{len(relevant_papers)}</strong> relevant &nbsp;·&nbsp;
      <strong>{must_reads}</strong> must-read
    </p>
  </div>

  {papers_html}

  {fallback_html}

  <div style="margin-top:30px;padding:14px;background:#f0f0f0;border-radius:6px;
              font-size:11px;color:#888;text-align:center">
    Generated by GW Digest · Powered by Claude + arXiv API ·
    <a href="https://github.com/YOUR_USERNAME/gw-digest" style="color:#888">GitHub</a>
  </div>

</body></html>"""


def send_email(html_content, num_papers):
    if DRY_RUN:
        out_path = "/tmp/gw_digest_preview.html"
        with open(out_path, "w") as f:
            f.write(html_content)
        print(f"DRY RUN — digest saved to {out_path}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌌 GW Digest {date.today()} — {num_papers} papers"
    msg["From"]    = GMAIL_USER
    msg["To"]      = YOUR_EMAIL
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
    print(f"✅ Email sent to {YOUR_EMAIL}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  GW Daily Digest — {date.today()}")
    print(f"{'='*55}")

    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # 1. Fetch papers
    print("\n[1/3] Fetching recent papers...")
    papers = fetch_recent_papers()
    total_screened = len(papers)
    print(f"  Total to screen: {total_screened}")

    # 2. Score each paper
    print(f"\n[2/3] Scoring papers (threshold ≥ {RELEVANCE_THRESHOLD}/10)...")
    relevant = []
    skipped = 0
    for i, paper in enumerate(papers, 1):
        if not passes_keyword_filter(paper):
            skipped += 1
            print(f"  · [--/10] [filtered  ] {paper.title[:65]}...")
            continue
        sleep(0.4)  # gentle rate limiting
        score_info = score_paper(claude_client, paper)
        score = score_info.get("score", 0)
        priority = score_info.get("suggested_read_priority", "skim")
        icon = "✅" if score >= RELEVANCE_THRESHOLD else "·"
        print(f"  {icon} [{score:2d}/10] [{priority:9s}] {paper.title[:65]}...")

        if score >= RELEVANCE_THRESHOLD:
            summary = summarize_paper(claude_client, paper, score_info)
            relevant.append((paper, score_info, summary))

    # Sort by score descending
    relevant.sort(key=lambda x: x[1].get("score", 0), reverse=True)
    print(f"\n  → {len(relevant)} relevant papers found ({skipped} skipped by keyword filter)")

    # Save any 9–10 scorers to cache
    save_to_cache(relevant)

    # Check if we have any 9–10 papers today
    top_today = [r for r in relevant if r[1].get("score", 0) >= 9]
    fallback_entries = []
    if not top_today:
        today_ids = {p.entry_id.split("/")[-1] for p, _, _ in relevant}
        fallback_entries = get_fallback_papers(exclude_ids=today_ids)
        if fallback_entries:
            print(f"  → No 9–10 papers today, adding {len(fallback_entries)} from archive")

    # 3. Build and send digest
    print("\n[3/3] Building and sending digest...")
    if not relevant and not fallback_entries:
        print("  No relevant papers today — no email sent.")
        return

    html = build_html(relevant, total_screened, fallback_entries=fallback_entries)
    send_email(html, len(relevant))
    print("\nDone! 🎉\n")


if __name__ == "__main__":
    main()
