#!/usr/bin/env python3
"""
precompute_embeddings.py — RUN THIS ON YOUR LOCAL MACHINE (not in any sandbox)
================================================================================
Stage 2 upgrade: semantic embedding similarity between the JD and each
candidate's career narrative.

WHY THIS EXISTS AS A SEPARATE SCRIPT:
  The hackathon's compute constraint (≤5 min, presumably CPU-only at judging
  time) applies to the RANKING step, not to pre-computation you do beforehand.
  This script downloads a small sentence-transformer model ONCE, embeds all
  100K candidates ONCE, and caches the result to disk. Your actual `rank.py`
  then just loads the cached vectors and does a fast cosine-similarity sort —
  well within any time budget.

  This is also exactly how a real production system would work: embeddings
  are computed in a batch/offline job, not synchronously per search request.

REQUIREMENTS (install once):
    pip install sentence-transformers numpy tqdm

MODEL CHOICE:
    all-MiniLM-L6-v2 — 80MB, CPU-friendly, ~30ms/sentence on a laptop CPU,
    good general-purpose semantic quality. This is NOT the largest or fanciest
    model available — it's chosen because it runs comfortably on 16GB RAM/no
    GPU and the marginal quality gain from a bigger model rarely justifies the
    10-50x slowdown for a candidate-ranking use case.

WHAT IT PRODUCES:
    candidate_embeddings.npy   — (100000, 384) float32 array
    candidate_ids.json         — ordered list of candidate_ids matching the rows above
    jd_embedding.npy           — (384,) float32 vector for the job description

RUNTIME ESTIMATE (your 16GB CPU machine):
    ~100,000 candidates × ~30ms each ≈ 50 minutes single-threaded.
    With batching (this script batches 64 at a time) expect 10-20 minutes.
    Run it once, overnight if you like — rank.py reuses the cache forever
    until you change the candidate data or the JD.

USAGE:
    python precompute_embeddings.py \
        --candidates /path/to/candidates.jsonl \
        --jd_text /path/to/jd_summary.txt \
        --out_dir ./embeddings_cache
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("Missing numpy. Run: pip install numpy", file=sys.stderr)
    sys.exit(1)


# The "ideal candidate" distillation from the JD — this is what gets embedded
# and compared against. Written from the JD's own "How to read between the
# lines" section, not just the skills list.
DEFAULT_JD_SUMMARY = """
Senior AI Engineer with 6-8 years of total experience, of which 4-5 years are
in applied ML/AI roles at product companies (not pure services/consulting).
Has production experience deploying embeddings-based retrieval systems
(sentence-transformers, OpenAI embeddings, BGE, E5) to real users, handling
embedding drift, index refresh, and retrieval-quality regression in production.
Has hands-on production experience with vector databases or hybrid search
infrastructure (Pinecone, Weaviate, Qdrant, Milvus, OpenSearch, Elasticsearch,
FAISS). Strong Python and code quality. Has designed evaluation frameworks for
ranking systems: NDCG, MRR, MAP, offline-to-online correlation, A/B test
interpretation. Has shipped at least one end-to-end ranking, search, or
recommendation system to real users at meaningful scale. Has strong informed
opinions about retrieval (hybrid vs dense), evaluation (offline vs online),
and LLM integration (when to fine-tune vs prompt), grounded in systems they
actually built — not framework tutorials or demo projects. Stable career
trajectory (not switching companies every 1.5 years purely for title
progression). Currently writes production code, has not drifted into a purely
architectural or tech-lead role without hands-on work in the last 18 months.
Located in or willing to relocate to Noida or Pune, India; Hyderabad, Mumbai,
Delhi NCR also acceptable.
"""


def build_career_narrative(candidate):
    """Concatenate the candidate's career story into a single text block
    suitable for embedding — title, company, and description per role,
    most recent role weighted by being placed first."""
    profile = candidate["profile"]
    history = candidate.get("career_history", [])
    try:
        history = sorted(history, key=lambda r: r.get("start_date", ""), reverse=True)
    except Exception:
        pass

    parts = [
        f"{profile.get('headline', '')}.",
        f"{profile.get('summary', '')}",
    ]
    for role in history:
        parts.append(
            f"{role.get('title', '')} at {role.get('company', '')} "
            f"({role.get('duration_months', 0)} months): {role.get('description', '')}"
        )
    return " ".join(p for p in parts if p.strip())


def main():
    parser = argparse.ArgumentParser(description="Pre-compute candidate embeddings")
    parser.add_argument("--candidates", required=True, help="Path to candidates.jsonl")
    parser.add_argument("--jd_text", default=None,
                        help="Path to a text file with the JD summary. If omitted, uses the built-in default.")
    parser.add_argument("--out_dir", default="./embeddings_cache")
    parser.add_argument("--model", default="all-MiniLM-L6-v2")
    parser.add_argument("--batch_size", type=int, default=64)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading sentence-transformers model (first run downloads ~80MB)...")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("Missing sentence-transformers. Run: pip install sentence-transformers", file=sys.stderr)
        sys.exit(1)

    model = SentenceTransformer(args.model)
    print(f"Model loaded: {args.model} (dim={model.get_sentence_embedding_dimension()})")

    jd_summary = DEFAULT_JD_SUMMARY
    if args.jd_text:
        jd_summary = Path(args.jd_text).read_text(encoding="utf-8")

    print("Embedding JD summary...")
    jd_embedding = model.encode([jd_summary], normalize_embeddings=True)[0]
    np.save(out_dir / "jd_embedding.npy", jd_embedding.astype("float32"))

    print(f"Loading candidates from {args.candidates}...")
    candidates = []
    with open(args.candidates, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                candidates.append(json.loads(line))
    print(f"Loaded {len(candidates):,} candidates.")

    candidate_ids = [c["candidate_id"] for c in candidates]
    texts = [build_career_narrative(c) for c in candidates]

    print(f"Encoding {len(texts):,} career narratives "
          f"(batch_size={args.batch_size})... this will take a while.")
    start = time.time()

    try:
        from tqdm import tqdm
        embeddings = model.encode(
            texts, batch_size=args.batch_size, normalize_embeddings=True,
            show_progress_bar=True, convert_to_numpy=True
        )
    except ImportError:
        embeddings = model.encode(
            texts, batch_size=args.batch_size, normalize_embeddings=True,
            convert_to_numpy=True
        )

    elapsed = time.time() - start
    print(f"Done in {elapsed/60:.1f} minutes ({elapsed/len(texts)*1000:.1f}ms/candidate).")

    np.save(out_dir / "candidate_embeddings.npy", embeddings.astype("float32"))
    with open(out_dir / "candidate_ids.json", "w") as f:
        json.dump(candidate_ids, f)

    print(f"\nCached to {out_dir}/:")
    print(f"  candidate_embeddings.npy  ({embeddings.shape})")
    print(f"  candidate_ids.json        ({len(candidate_ids)} ids)")
    print(f"  jd_embedding.npy          ({jd_embedding.shape})")
    print("\nNext: run rank_with_embeddings.py to produce the final ranked submission.csv")


if __name__ == "__main__":
    main()
