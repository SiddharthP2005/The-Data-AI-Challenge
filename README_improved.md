# Redrob AI Candidate Ranker
## Redrob Hackathon 2026 — Intelligent Candidate Discovery & Ranking Challenge

### What it does
Ranks 100,000 candidates against the Senior AI Engineer JD by understanding recruiter intent, not just keyword overlap.

---

## Architecture

Candidates.jsonl
        |
Feature Extraction
        |
------------------------------------------------
| Career Substance (35%)                       |
| Skills + Verified Assessments (25%)          |
| Trajectory Integrity (10%)                   |
| Experience Band (10%)                        |
| Behavioral Availability (15%)                |
| External Validation (5%)                     |
------------------------------------------------
        |
Anti‑Pattern Penalty
        |
Composite Score
        |
Top‑100 Candidates

### Scoring Formula

Score =
0.35×CareerSubstance +
0.25×Skills +
0.10×Trajectory +
0.10×Experience +
0.15×Behavior +
0.05×ExternalValidation

FinalScore = Score × PenaltyMultiplier

---

## Compute Constraints

- Runtime: ~60 seconds for 100K candidates
- RAM: ~800 MB peak
- No GPU required
- Zero external API calls

---

## Why this matches Redrob's philosophy

Traditional ATS:
JD keywords → Candidate keywords

Our approach:
JD intent → Career history → Behavioral signals → Anti‑patterns → Ranking

Skills alone are insufficient.
Career trajectory, verified assessments, and recruiter signals matter.

---

## Output

candidate_id, rank, score, reasoning

Example:

CAND_0071974,1,0.8804,"7.8yr Senior AI Engineer with production retrieval experience"

