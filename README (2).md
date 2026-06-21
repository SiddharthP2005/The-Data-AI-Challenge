# Redrob AI Candidate Ranker
**Redrob Hackathon 2026 — Intelligent Candidate Discovery & Ranking Challenge**

## What it does

Ranks 100,000 candidates against the Senior AI Engineer JD by reading the JD
the way a recruiter actually would — including its **eight explicit
disqualifiers** buried in prose, not just its skills checklist.

> *"The right answer involves reasoning about the gap between what the JD
> says and what the JD means. A candidate who has all the AI keywords listed
> as skills but whose title is 'Marketing Manager' is not a fit, no matter
> how perfect their skill list looks."* — job_description.docx

## Two layers in this repo

| Layer | Where | Compute | Status |
|-------|-------|---------|--------|
| **Rule-based ranker** | `rank.py` (repo root) | CPU-only, 60s for 100K, no downloads | Run, validated, this is `submission.csv` |
| **Semantic + LLM upgrade** | `local_upgrade/` | Needs a one-time model download + your own CPU/GPU | Code complete & unit-tested, run-it-yourself |

**Why split this way:** the build environment used to produce the official
submission has no access to model-hosting domains (huggingface.co etc.), so
the embedding/LLM stages genuinely cannot execute there. Rather than
hand-wave a "we used embeddings" claim with no way to prove it, the rule-based
layer is what's actually been run end-to-end against the full 100K-row
dataset and validated, and the upgrade scripts are provided separately with
their own README, tested via synthetic fixtures, for you to run for the
extra fidelity if you have the compute.

## Quick start (rule-based ranker)

```bash
python rank.py --candidates candidates.jsonl --out submission.csv
python validate_submission.py submission.csv
```

Runtime: ~60 seconds for 100,000 candidates. No external API calls, no GPU.

## Architecture

Six scoring modules, weighted composite, plus a multiplicative anti-pattern
penalty:

| Module | Weight | What it measures |
|--------|--------|-------------------|
| Career Substance | 35% | Title relevance, ML-keyword density in role descriptions, product-company history |
| Skills + Verified Assessment | 25% | Must-have/nice-to-have skills, blended with `skill_assessment_scores` (verified, not self-reported) |
| Trajectory Integrity | 10% | Penalizes title-chasing (escalating titles via sub-18-month job hops) |
| Experience Band | 10% | 5-9yr sweet spot per the JD's explicit framing |
| Behavioral Availability | 15% | All 23 `redrob_signals` fields — recency, response rate, notice period, location, verification |
| External Validation | 5% | `github_activity_score` as a "show your thinking" proxy |
| **Anti-Pattern Penalty** | multiplier (up to -85%) | The 8 explicit JD disqualifiers, see below |

### The 8 JD disqualifiers, and how each is detected

| # | Disqualifier (verbatim from JD) | Detection logic | Fires on this dataset? |
|---|----------------------------------|------------------|--------------------------|
| 1 | Pure research, no production deployment | Research-lab keywords present + zero deployment-language hits in career text | No - dataset's synthetic descriptions always include production language |
| 2 | Recent-only (<12mo) LangChain/OpenAI-wrapper "AI experience" with no pre-LLM ML background | LangChain/wrapper keyword + absence of classical-ML keywords + short recent tenure | No - dataset doesn't generate LangChain-mention text |
| 3 | Senior engineer, no production code in 18+ months (architecture/tech-lead drift) | Architect/tech-lead title + 18mo+ tenure + no "hands-on" language | No - dataset's title vocabulary (48 fixed titles) has no Architect/Tech Lead title |
| 4 | Title-chasing: Senior to Staff to Principal via sub-1.5yr job hops | Escalating seniority rank across roles + 60%+ of roles under 18 months | **Yes - 5.1% of candidates** |
| 5 | "Framework enthusiast": tutorial-style GitHub, demo blog posts | Tutorial/demo-blog language in career text or summary | No - not present in this dataset's generated text |
| 6 | 100% consulting-firm career, no product-company experience | All roles at TCS/Infosys/Wipro/Accenture/Cognizant/Capgemini | **Yes - 9.0% of candidates** |
| 7 | CV/speech/robotics primary expertise, no NLP/IR | 2+ CV/speech skills, 0 IR/NLP skills | **Yes - 2.5% of candidates** |
| 8 | 5+ years entirely closed-source, zero external validation | 5+ YoE, no GitHub linked (`github_activity_score == -1`), no OSS/publication language | **Yes - 43.4% of candidates** (soft penalty, not hard reject - lacking GitHub alone is weak evidence) |

All 8 detectors were tested against the full 100K-row dataset before
finalizing weights. Four never fire because this particular synthetic
dataset doesn't generate text matching those patterns (no Architect titles
exist among the 48 titles in the data, for example) - that's reported
honestly rather than left as untested, unverifiable code. The four that do
fire are weighted into the penalty multiplier and visibly affect rankings.

### Why `skill_assessment_scores` matters

Self-reported skill proficiency ("expert" in 10 skills) is exactly the
"keyword stuffing" the JD warns about. The dataset includes a separate
`skill_assessment_scores` field - a verified, per-skill 0-100 score from
Redrob's own assessment platform. Our skills module blends self-reported
proficiency (40% weight) with this verified score (60% weight) wherever
both exist for a skill, so a candidate who claims "expert" but scores 20/100
on the actual assessment is scored accordingly - not at face value.

## Output

`submission.csv` / `submission.xlsx` - 100 rows, ranked best-fit first:

```
candidate_id,rank,score,reasoning
CAND_0071974,1,0.8804,"7.8yr Senior AI Engineer at Netflix with production embedding/retrieval experience (learning to rank, weaviate)"
```

## Results summary (top 100)

- **#1: Senior AI Engineer, Netflix, 7.8yr, score 0.8804**
- 94/100 India-based; 15/100 specifically in Pune or Noida (the JD's stated preference)
- Mean YoE: 6.5 (within the JD's 5-9yr band)
- Mean recruiter response rate: 0.66
- 35/100 have notice period <=30 days (the JD's stated ideal)
- 92/100 have a linked GitHub with verifiable activity (mean score 57.9/100)
- Zero candidates in the top 100 trip the title-chasing or consulting-only detectors
- Top companies represented: CRED, Freshworks, Zoho, Flipkart, Amazon, Meta, Netflix

## Compute constraints

| Constraint | Target | Actual |
|------------|--------|--------|
| Runtime | <=5 min | **~60 seconds** |
| RAM | <=16 GB | ~800 MB peak |
| GPU | None required | Pure Python, no torch |
| External API calls | None | Zero network calls |

## Repo structure

```
.
├── rank.py                          # Main ranker (rule-based, validated, this produced submission.csv)
├── submission.csv                   # Top-100 ranked output
├── submission.xlsx                  # Same data, formatted spreadsheet
├── validate_submission.py           # Format validator (from hackathon bundle)
├── README.md                        # This file
├── redrob_ranker_deck.pdf           # Approach explainer (also .pptx)
└── local_upgrade/                   # Stage 2/3: run on your own machine
    ├── README.md                    # Setup + fusion strategy explained
    ├── precompute_embeddings.py     # Sentence-transformer embedding cache builder
    ├── rank_with_embeddings.py      # Hybrid rule-based + semantic ranker
    └── llm_rerank.py                # Ollama chain-of-thought re-ranker (top-K shortlist only)
```

## What v2/v3 add, honestly

The local_upgrade scripts are **code-complete and unit-tested** (verified
end-to-end with synthetic embedding fixtures - see their README for what
"tested" means here) but were **not run against the real 100K dataset**
inside this build environment, because that environment cannot reach
huggingface.co to download the embedding model. If you have a machine with
internet access, running them takes the architecture from "rule-based only"
to a genuine three-stage pipeline: rules -> semantic recall -> LLM judgment -
which is also literally the v1->v2->v3 roadmap the JD describes for Redrob's
own product.
