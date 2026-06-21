#!/usr/bin/env python3
"""
rank_with_embeddings.py — RUN THIS ON YOUR LOCAL MACHINE, AFTER precompute_embeddings.py
============================================================================================
Stage 2 ranker: combines the rule-based score from rank.py with semantic
cosine similarity between each candidate's career narrative and the JD.

This is an ADDITIVE upgrade, not a replacement — the rule-based score still
catches the explicit JD disqualifiers (title-chasing, consulting-only, etc.)
that a pure embedding similarity would miss entirely (a perfectly-worded
profile from a disqualified candidate can still score high on cosine sim).

FUSION STRATEGY:
    final_score = 0.65 * rule_based_score + 0.35 * semantic_similarity

    The rule-based score keeps the explicit weight (65%) because it directly
    encodes the JD's stated disqualifiers and behavioral-availability logic
    that embeddings cannot see. Semantic similarity (35%) adds recall for
    candidates whose career narrative matches the JD's intent without using
    its exact vocabulary — e.g., a candidate who writes "built a system that
    surfaces the most relevant items to users in real time" instead of using
    the word "ranking" explicitly.

PREREQUISITE:
    Run precompute_embeddings.py first to generate:
      embeddings_cache/candidate_embeddings.npy
      embeddings_cache/candidate_ids.json
      embeddings_cache/jd_embedding.npy

USAGE:
    python rank_with_embeddings.py \
        --candidates /path/to/candidates.jsonl \
        --embeddings_dir ./embeddings_cache \
        --out submission_v3.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

# Reuse the exact scoring logic from the validated rule-based ranker.
# Place rank.py in the same directory (it's included in this repo).
sys.path.insert(0, str(Path(__file__).parent.parent))
from rank import score_candidate, generate_reasoning, TODAY  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Hybrid rule-based + embedding ranker")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--embeddings_dir", default="./embeddings_cache")
    parser.add_argument("--out", default="submission_v3.csv")
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--rule_weight", type=float, default=0.65)
    parser.add_argument("--semantic_weight", type=float, default=0.35)
    args = parser.parse_args()

    emb_dir = Path(args.embeddings_dir)
    print("Loading pre-computed embeddings...")
    candidate_embeddings = np.load(emb_dir / "candidate_embeddings.npy")
    with open(emb_dir / "candidate_ids.json") as f:
        candidate_ids = json.load(f)
    jd_embedding = np.load(emb_dir / "jd_embedding.npy")

    # Cosine similarity (embeddings already normalized at encode time)
    similarities = candidate_embeddings @ jd_embedding
    sim_by_id = dict(zip(candidate_ids, similarities.tolist()))
    print(f"Loaded {len(candidate_ids):,} embeddings. "
          f"Similarity range: [{similarities.min():.3f}, {similarities.max():.3f}]")

    print(f"Loading candidates from {args.candidates}...")
    scored = []
    total, errors = 0, 0
    with open(args.candidates, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                candidate = json.loads(line)
                cid = candidate["candidate_id"]
                rule_score, components = score_candidate(candidate)
                semantic_score = sim_by_id.get(cid, 0.0)
                # Cosine sim is typically in [0.1, 0.7] for this kind of text;
                # rescale to roughly [0,1] for fair blending with the rule score.
                semantic_norm = max(0.0, min(1.0, (semantic_score - 0.1) / 0.5))

                final = args.rule_weight * rule_score + args.semantic_weight * semantic_norm
                components["semantic_similarity"] = round(semantic_score, 4)
                scored.append((round(final, 4), candidate, components))
                total += 1
                if total % 10000 == 0:
                    print(f"  Processed {total:,}...")
            except Exception:
                errors += 1

    print(f"Processed {total:,} candidates ({errors} errors).")

    scored.sort(key=lambda x: (-x[0], x[1]["candidate_id"]))
    top100 = scored[:args.top]

    print(f"Writing top {args.top} to {args.out}...")
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["candidate_id", "rank", "score", "reasoning"])
        for rank_idx, (score, candidate, components) in enumerate(top100, start=1):
            reasoning = generate_reasoning(candidate, components, rank_idx)
            sim = components.get("semantic_similarity", 0)
            reasoning_with_sim = f"{reasoning}; semantic_sim={sim:.3f}"
            writer.writerow([candidate["candidate_id"], rank_idx,
                            f"{score:.4f}", reasoning_with_sim[:300]])

    print(f"Done. Top score: {top100[0][0]:.4f}, rank-100: {top100[-1][0]:.4f}")
    print("\nTop 10:")
    for rank_idx, (score, candidate, components) in enumerate(top100[:10], start=1):
        p = candidate["profile"]
        print(f"{rank_idx:>3} {candidate['candidate_id']} {score:.4f} "
              f"rule={components['career']+components['skills']:.2f} "
              f"sem={components.get('semantic_similarity', 0):.3f}  "
              f"{p['current_title'][:30]}")


if __name__ == "__main__":
    main()
