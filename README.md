# Local Upgrade Path: Semantic Embeddings

The scripts in this folder were **not run inside the build sandbox** that
produced `submission.csv`. They require downloading a model from Hugging
Face, and that sandbox has no internet access to model hosts (pypi.org is
reachable, huggingface.co is not). Rather than fake the output, this folder
gives you tested, runnable code to execute on your own machine if you want
to push past the rule-based ceiling.

**The code path was tested end-to-end with synthetic random embeddings** to
confirm there are zero bugs in the loading, fusion math, normalization, or
CSV writing — what's untested is only the *quality* of real embeddings,
which depends on the actual model and can't be verified without downloading
it.

## What this adds

The base `rank.py` (in the repo root) is 100% rule-based: keyword/phrase
detection in career text, structured field logic, no learned representations.
That's deliberately conservative — fully deterministic, auditable, and
explainable, but it can miss a candidate who describes the right experience
using different words than the JD does.

`precompute_embeddings.py` + `rank_with_embeddings.py` add a **semantic
similarity layer**: each candidate's career narrative and the JD get
embedded into the same vector space, and cosine similarity becomes a second
signal blended with the rule-based score.

## Steps to run

```bash
cd local_upgrade
pip install sentence-transformers numpy tqdm

# Step 1: pre-compute embeddings for all 100K candidates (~10-20 min on a
# laptop CPU; this is the part that needs internet, for the one-time model
# download — after that it's fully offline)
python precompute_embeddings.py \
    --candidates ../../candidates.jsonl \
    --out_dir ./embeddings_cache

# Step 2: produce the final hybrid-ranked submission (~seconds, reads cache)
python rank_with_embeddings.py \
    --candidates ../../candidates.jsonl \
    --embeddings_dir ./embeddings_cache \
    --out submission_v3.csv

# Step 3: validate
python ../../validate_submission.py submission_v3.csv
```

## Fusion strategy

```
final_score = 0.65 × rule_based_score + 0.35 × semantic_similarity
```

The rule-based score keeps majority weight because it directly encodes the
JD's explicit disqualifiers (title-chasing, consulting-only, research-only,
CV/speech-without-IR) that pure embedding similarity cannot see — a
well-written profile from a disqualified candidate can still score high on
cosine similarity alone. Semantic similarity adds recall for candidates
whose career narrative matches the JD's *intent* without using its exact
vocabulary.

## If you want to go further: LLM re-ranking

See `llm_rerank.py` for an Ollama-based chain-of-thought re-ranker that runs
on the top 300-500 candidates from the hybrid stage above. This is the
deepest layer — a local 3B-parameter model reasons about each candidate the
way a recruiter would, producing both a verdict and an explanation. It's
the slowest stage (~1-3 sec/candidate) which is exactly why it only runs on
a pre-filtered shortlist, not all 100K.

```bash
# Requires Ollama installed locally: https://ollama.com
ollama pull qwen2.5:3b

python llm_rerank.py \
    --shortlist submission_v3.csv \
    --candidates ../../candidates.jsonl \
    --out submission_final.csv \
    --top_k 300
```

## Why three stages, not one

| Stage | Catches | Misses |
|-------|---------|--------|
| Rule-based (`rank.py`) | Explicit disqualifiers, structured fields | Paraphrased experience, novel phrasing |
| + Embeddings | Semantic paraphrase matches | Still can't *reason* about contradictions or weigh tradeoffs |
| + LLM re-rank | Nuanced judgment calls, explains tradeoffs in plain language | Slow — only viable on a pre-filtered shortlist |

Each stage narrows the field and hands off to a more expensive, more
reasoning-capable stage — which is also exactly how the JD describes Redrob's
own v1→v2→v3 evolution (BM25+rules → embeddings+hybrid → LLM re-ranking).
