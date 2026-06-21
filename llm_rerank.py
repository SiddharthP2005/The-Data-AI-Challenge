#!/usr/bin/env python3
"""
llm_rerank.py — RUN THIS ON YOUR LOCAL MACHINE, AFTER rank_with_embeddings.py
=================================================================================
Stage 3 upgrade: LLM chain-of-thought re-ranking on a pre-filtered shortlist.

WHY ONLY ON A SHORTLIST, NOT ALL 100K:
    Even a small local LLM (3B params) takes 1-3 seconds per candidate on
    CPU. Running that on 100,000 candidates would take 30-80+ hours. Running
    it on the top 300 from the hybrid stage takes 10-25 minutes — and those
    300 are already the only candidates that could plausibly end up in the
    final top 100, so nothing of value is lost by not scoring the other
    99,700 with the expensive model.

REQUIREMENTS:
    1. Install Ollama: https://ollama.com (free, runs locally, no API key)
    2. Pull a small model:  ollama pull qwen2.5:3b
       (or llama3.2:3b — both run comfortably in 16GB RAM, no GPU needed)
    3. pip install requests

WHAT IT DOES:
    For each candidate in the shortlist, sends a structured prompt containing
    the JD's actual disqualifier list (verbatim from job_description.docx)
    and the candidate's career history, and asks the model to:
      a) Identify which (if any) disqualifiers apply
      b) Give a 1-10 fit score with reasoning
      c) Flag anything the rule-based system might have missed

    This catches nuance the rule-based regex/keyword approach cannot —
    e.g., a candidate whose description says "I mostly focus on system
    design now and pair with juniors on implementation" reads as architecture
    drift to a human but might not trip a keyword-based detector.

OUTPUT FORMAT MATCHES validate_submission.py exactly.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing requests. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


OLLAMA_URL = "http://localhost:11434/api/generate"

PROMPT_TEMPLATE = """You are a senior technical recruiter evaluating a candidate for this role:

ROLE: Senior AI Engineer at Redrob AI (Series A, Pune/Noida, India)
IDEAL PROFILE: 6-8 years experience, 4-5 of which in applied ML/AI at product
companies (not pure consulting). Has shipped an end-to-end ranking, search,
or recommendation system to real users. Production experience with
embeddings-based retrieval AND vector databases/hybrid search. Strong Python.
Has designed evaluation frameworks (NDCG/MRR/MAP, A/B testing).

EXPLICIT DISQUALIFIERS — reject if ANY apply:
1. Pure research background, no production deployment ever
2. "AI experience" is only <12mo of LangChain/OpenAI-wrapper work, with no
   pre-LLM-era ML production background (no XGBoost, collaborative filtering,
   classical NLP, etc.)
3. Senior engineer who hasn't written production code in 18+ months
   (drifted into pure architecture/tech-lead with no hands-on work)
4. Title-chasing: switches companies every ~1.5 years purely to escalate
   seniority titles (Senior -> Staff -> Principal), no real depth at any one
5. Career is 100% at consulting firms (TCS, Infosys, Wipro, Accenture,
   Cognizant, Capgemini) with zero product-company experience
6. Primary expertise is computer vision, speech, or robotics with no
   significant NLP/IR exposure

CANDIDATE:
Title: {title}
Company: {company}
Years of experience: {yoe}
Career history:
{career_history}

Skills: {skills}

Respond ONLY with valid JSON in this exact format, no other text:
{{"disqualifiers_triggered": ["list any of the 6 numbered disqualifiers that clearly apply, empty list if none"], "fit_score": <integer 1-10>, "reasoning": "<one or two sentences explaining the score, recruiter tone>"}}
"""


def format_career_history(candidate):
    lines = []
    history = candidate.get("career_history", [])
    try:
        history = sorted(history, key=lambda r: r.get("start_date", ""), reverse=True)
    except Exception:
        pass
    for role in history:
        lines.append(
            f"- {role.get('title', '')} at {role.get('company', '')} "
            f"({role.get('duration_months', 0)} months): {role.get('description', '')[:300]}"
        )
    return "\n".join(lines)


def call_ollama(prompt, model="qwen2.5:3b", timeout=30, retries=2):
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.1}},
                timeout=timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            # Extract JSON from response (model may wrap it in markdown fences)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return None
        except Exception as e:
            if attempt == retries:
                print(f"  Ollama call failed after {retries} retries: {e}", file=sys.stderr)
                return None
            time.sleep(1)
    return None


def main():
    parser = argparse.ArgumentParser(description="LLM re-ranker for shortlisted candidates")
    parser.add_argument("--shortlist", required=True,
                        help="CSV from rank_with_embeddings.py (or rank.py)")
    parser.add_argument("--candidates", required=True, help="Path to candidates.jsonl")
    parser.add_argument("--out", default="submission_final.csv")
    parser.add_argument("--top_k", type=int, default=300,
                        help="How many from the shortlist to re-rank with the LLM")
    parser.add_argument("--final_n", type=int, default=100)
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()

    print(f"Checking Ollama is running at {OLLAMA_URL}...")
    try:
        requests.get("http://localhost:11434", timeout=3)
    except Exception:
        print("ERROR: Ollama doesn't appear to be running. Start it with: ollama serve",
              file=sys.stderr)
        sys.exit(1)

    shortlist_ids = []
    with open(args.shortlist) as f:
        for row in csv.DictReader(f):
            if int(row["rank"]) <= args.top_k:
                shortlist_ids.append(row["candidate_id"])
    print(f"Loaded shortlist: {len(shortlist_ids)} candidates to re-rank.")

    id_set = set(shortlist_ids)
    candidates = {}
    with open(args.candidates) as f:
        for line in f:
            c = json.loads(line)
            if c["candidate_id"] in id_set:
                candidates[c["candidate_id"]] = c
            if len(candidates) == len(id_set):
                break

    print(f"Matched {len(candidates)} candidate records. Calling {args.model} via Ollama...")
    results = []
    start = time.time()

    for i, cid in enumerate(shortlist_ids, 1):
        c = candidates.get(cid)
        if c is None:
            continue
        profile = c["profile"]
        skills = ", ".join(s["name"] for s in c.get("skills", []))

        prompt = PROMPT_TEMPLATE.format(
            title=profile.get("current_title", ""),
            company=profile.get("current_company", ""),
            yoe=profile.get("years_of_experience", ""),
            career_history=format_career_history(c),
            skills=skills,
        )

        verdict = call_ollama(prompt, model=args.model)
        if verdict is None:
            verdict = {"disqualifiers_triggered": [], "fit_score": 5,
                      "reasoning": "LLM call failed; defaulted to neutral score."}

        results.append((cid, c, verdict))

        if i % 25 == 0:
            elapsed = time.time() - start
            rate = elapsed / i
            remaining = rate * (len(shortlist_ids) - i)
            print(f"  {i}/{len(shortlist_ids)} done, "
                  f"~{remaining/60:.1f} min remaining...")

    print(f"LLM re-ranking complete in {(time.time()-start)/60:.1f} minutes.")

    # Sort by: disqualified candidates last, then by fit_score desc, then candidate_id
    def sort_key(item):
        cid, c, verdict = item
        disqualified = len(verdict.get("disqualifiers_triggered", [])) > 0
        fit = verdict.get("fit_score", 0)
        return (disqualified, -fit, cid)

    results.sort(key=sort_key)
    final = results[:args.final_n]

    print(f"Writing top {len(final)} to {args.out}...")
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["candidate_id", "rank", "score", "reasoning"])
        for rank_idx, (cid, c, verdict) in enumerate(final, start=1):
            fit = verdict.get("fit_score", 5)
            score = round(fit / 10.0, 4)
            reasoning = verdict.get("reasoning", "")[:280]
            dq = verdict.get("disqualifiers_triggered", [])
            if dq:
                reasoning = f"[DQ flags: {dq}] " + reasoning
            writer.writerow([cid, rank_idx, f"{score:.4f}", reasoning])

    print("Done.")


if __name__ == "__main__":
    main()
