#!/usr/bin/env python3
"""
Redrob Intelligent Candidate Ranker — v2
==========================================
A hybrid multi-signal scorer built by reading the JD as a recruiter would —
including its explicit disqualifiers, not just its skills list.

Why v2 exists:
  The JD (job_description.docx) contains EIGHT explicit disqualifiers that a
  naive skills-matcher would never see, because they require reading the
  *career narrative*, not the skills list:

    1. Pure research / no production deployment            -> reject
    2. "AI experience" = <12mo of LangChain+OpenAI wrapping,
       with no pre-LLM-era ML production background         -> reject
    3. Senior engineer, no production code in 18 months
       (drifted into pure architecture/tech-lead)            -> reject
    4. Title-chasing: Senior->Staff->Principal by switching
       companies every ~1.5 years                            -> reject
    5. "Framework enthusiast": LangChain-tutorial GitHub,
       demo blog posts, no systems thinking                  -> penalize
    6. Consulting-only career (TCS/Infosys/Wipro/Accenture/
       Cognizant/Capgemini) with NO prior product company    -> reject
    7. CV / speech / robotics primary expertise without
       significant NLP/IR exposure                           -> reject
    8. 5+ years entirely on closed-source systems with ZERO
       external validation (no GitHub, no papers, no talks)  -> penalize

  These disqualifiers are detected from career_history descriptions,
  duration_months sequences, and the github_activity_score signal —
  not from the skills array, which is exactly the trap the JD warns about.

Architecture (unchanged from v1, refined):
  1. Career signal scoring   (35%) — title relevance, ML substance, product-co history,
                                      research-only detection, code-recency detection
  2. Skills + assessment      (25%) — must-have/nice-to-have, weighted by VERIFIED
                                      skill_assessment_scores (not just self-reported proficiency)
  3. Trajectory integrity     (10%) — title-chaser detection via job-tenure sequence
  4. Experience band fit      (10%) — 5-9yr sweet spot per JD's explicit framing
  5. Behavioral availability  (15%) — all 23 redrob_signals fields, used as availability multiplier
  6. External validation      ( 5%) — github_activity_score as "show your thinking" proxy
  7. Anti-pattern penalty     (multiplier, up to -85%) — disqualifiers above

No external APIs, no GPU, no model downloads. Pure Python stdlib + light text
heuristics. Deterministic and reproducible: same input -> same output, always.
"""

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

TODAY = date(2026, 6, 11)

# ─── JD-derived vocabularies ──────────────────────────────────────────────────

MUST_HAVE_SKILLS = {
    "embeddings", "embedding", "sentence-transformers", "sentence transformers",
    "openai embeddings", "bge", "e5",
    "pinecone", "weaviate", "qdrant", "milvus", "faiss", "opensearch",
    "elasticsearch", "chroma", "chromadb", "pgvector",
    "information retrieval", "retrieval", "ranking", "learning to rank",
    "hybrid search", "bm25", "reranking", "re-ranking",
    "nlp", "natural language processing", "transformers", "hugging face",
    "huggingface", "llm", "large language models", "rag",
    "retrieval augmented generation", "python",
}

NICE_HAVE_SKILLS = {
    "lora", "qlora", "peft", "fine-tuning", "fine tuning", "finetuning",
    "xgboost", "lightgbm", "ndcg", "mrr", "map", "a/b testing",
    "pytorch", "tensorflow", "distributed systems", "mlops", "mlflow",
    "recommendation systems", "recommendation", "search",
    "kafka", "spark", "airflow", "dbt", "scikit-learn", "sklearn",
    "vector search", "semantic search", "hr-tech", "recruiting tech",
    "marketplace", "large-scale inference", "inference optimization",
}

EVAL_SKILLS = {
    "ndcg", "mrr", "map", "a/b testing", "offline evaluation",
    "online evaluation", "evaluation framework", "ranking evaluation",
}

CONSULTING_FIRMS = {
    "tcs", "tata consultancy", "infosys", "wipro", "accenture",
    "cognizant", "capgemini", "tech mahindra", "hexaware", "mphasis",
    "l&t infotech", "ltimindtree", "mindtree",
}

PRODUCT_COMPANIES = {
    "google", "meta", "microsoft", "amazon", "apple", "netflix", "uber",
    "flipkart", "swiggy", "zomato", "paytm", "razorpay", "freshworks",
    "zoho", "ola", "meesho", "cred", "phonepe", "groww", "sharechat",
    "mad street den", "sarvam", "krutrim", "haptik",
    "unacademy", "byju", "dream11", "nykaa", "myntra", "bigbasket",
    "dunzo", "rapido", "slice", "smallcase", "zepto",
}

CAREER_ML_KEYWORDS = {
    "embedding", "embeddings", "vector", "retrieval", "ranking", "reranking",
    "recommendation", "nlp", "language model", "llm", "transformer",
    "fine-tuning", "fine tuning", "rag", "semantic search", "faiss",
    "pinecone", "weaviate", "qdrant", "milvus", "elasticsearch",
    "sentence-transformer", "ndcg", "mrr", "a/b test", "inference",
    "model deployment", "production ml", "ml pipeline", "feature engineering",
    "learning to rank", "xgboost", "lightgbm", "pytorch", "hugging face",
    "openai", "cohere", "pgvector", "opensearch",
}

DEPLOYMENT_SIGNALS = {
    "production", "deployed", "shipped", "a/b test", "inference",
    "latency", "serving", "pipeline", "users", "scale", "live",
}

# Disqualifier #2: recent-only LLM-wrapper signal
LANGCHAIN_WRAPPER_KEYWORDS = {
    "langchain", "openai api", "gpt wrapper", "chatgpt integration",
    "prompt engineering", "openai api integration",
}
PRE_LLM_ML_KEYWORDS = {
    "xgboost", "lightgbm", "random forest", "logistic regression",
    "collaborative filtering", "matrix factorization", "bm25",
    "tf-idf", "word2vec", "glove", "click-through", "ctr prediction",
    "feature engineering", "gradient boosting",
}

# Disqualifier #3: architecture/tech-lead drift (no recent hands-on code)
ARCHITECT_DRIFT_TITLES = {
    "architect", "tech lead", "engineering manager", "head of",
    "vp engineering", "director of engineering", "principal architect",
}

# Disqualifier #5: framework enthusiast
FRAMEWORK_TUTORIAL_SIGNALS = {
    "tutorial", "demo project", "built a demo", "side project using",
    "how i used", "blog post about",
}

# Disqualifier #7: CV/speech/robotics without NLP/IR
CV_SPEECH_ROBOTICS_SKILLS = {
    "computer vision", "image classification", "object detection",
    "image segmentation", "speech recognition", "tts", "asr", "robotics",
    "autonomous driving", "lidar", "slam",
}
IR_NLP_SKILLS = {
    "retrieval", "embeddings", "nlp", "information retrieval", "ranking",
    "natural language processing", "search", "semantic search",
}

NON_ML_TITLES = {
    "marketing manager", "operations manager", "project manager", "hr manager",
    "accountant", "civil engineer", "mechanical engineer", "graphic designer",
    "customer support", "content writer", "java developer", "frontend engineer",
    "business analyst", "data entry", "sales manager", "product manager",
}

PREFERRED_LOCATIONS = {"pune", "noida"}
ACCEPTABLE_LOCATIONS = {
    "hyderabad", "mumbai", "delhi", "ncr", "gurugram", "gurgaon",
    "bangalore", "bengaluru", "india",
}

ML_TITLE_KEYWORDS = {
    "ai engineer", "ml engineer", "machine learning", "nlp engineer",
    "data scientist", "research engineer", "applied scientist",
    "recommendation", "search engineer", "ranking engineer",
    "retrieval", "applied ml",
}


# ─── Helpers ───────────────────────────────────────────────────────────────

def days_since(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (TODAY - d).days
    except Exception:
        return 9999


def normalize(text):
    return text.lower().strip() if text else ""


def text_contains_any(text, keywords):
    t = normalize(text)
    return sum(1 for kw in keywords if kw in t)


def skill_set(candidate):
    return {normalize(s["name"]): s for s in candidate.get("skills", [])}


def career_text(candidate):
    parts = []
    for role in candidate.get("career_history", []):
        parts.append(normalize(role.get("title", "")))
        parts.append(normalize(role.get("description", "")))
    return " ".join(parts)


def is_consulting_only(candidate):
    history = candidate.get("career_history", [])
    if not history:
        return False
    consulting_count = sum(
        1 for role in history
        if any(cf in normalize(role.get("company", "")) for cf in CONSULTING_FIRMS)
    )
    return consulting_count == len(history)


def has_product_experience(candidate):
    for role in candidate.get("career_history", []):
        if any(pc in normalize(role.get("company", "")) for pc in PRODUCT_COMPANIES):
            return True
    return False


def detect_honeypot(candidate):
    score = 0.0
    skills = candidate.get("skills", [])
    yoe = candidate["profile"].get("years_of_experience", 0)

    expert_count = sum(1 for s in skills if s.get("proficiency") == "expert")
    if expert_count >= 8:
        score += 0.4

    for s in skills:
        dur_months = s.get("duration_months", 0)
        if dur_months > (yoe * 12 + 6):
            score += 0.3
            break

    if yoe < 2 and expert_count >= 3:
        score += 0.4
    if len(skills) > 20 and yoe < 4:
        score += 0.2

    # Cross-check: skill_assessment_scores vs claimed proficiency.
    # A candidate claiming "expert" but scoring low on the verified
    # assessment is a credibility red flag.
    assess = candidate["redrob_signals"].get("skill_assessment_scores", {}) or {}
    mismatch = 0
    checked = 0
    for s in skills:
        nm = s["name"]
        if nm in assess:
            checked += 1
            if s.get("proficiency") == "expert" and assess[nm] < 50:
                mismatch += 1
    if checked >= 3 and mismatch / max(checked, 1) > 0.5:
        score += 0.3

    return min(score, 1.0)


def detect_title_chasing(candidate):
    """
    Disqualifier #4: Senior -> Staff -> Principal by switching companies
    every ~1.5 years (18 months), purely for title progression.
    Returns a penalty in [0, 1].
    """
    history = candidate.get("career_history", [])
    if len(history) < 3:
        return 0.0

    # Sort by start_date
    try:
        sorted_hist = sorted(history, key=lambda r: r.get("start_date", ""))
    except Exception:
        sorted_hist = history

    short_stints = sum(
        1 for role in sorted_hist
        if 0 < role.get("duration_months", 999) <= 18
    )
    # Escalating seniority words
    seniority_words = ["junior", "engineer", "senior", "staff", "principal", "lead"]

    def seniority_rank(title):
        t = normalize(title)
        for i, w in enumerate(["junior", "engineer", "senior", "staff", "principal"]):
            if w in t:
                return i
        return 1

    ranks = [seniority_rank(r.get("title", "")) for r in sorted_hist]
    escalating = all(ranks[i] <= ranks[i + 1] for i in range(len(ranks) - 1)) and ranks[-1] > ranks[0]

    short_stint_ratio = short_stints / len(sorted_hist)

    if short_stint_ratio >= 0.6 and escalating and len(sorted_hist) >= 4:
        return 0.8
    elif short_stint_ratio >= 0.6:
        return 0.4
    return 0.0


def detect_research_only(candidate):
    """Disqualifier #1: pure research, no production deployment."""
    ctext = career_text(candidate)
    history = candidate.get("career_history", [])
    if not history:
        return 0.0

    research_signals = text_contains_any(
        ctext, {"research lab", "academic", "phd research", "publication",
                "research scientist", "research only"}
    )
    deployment_signals = text_contains_any(ctext, DEPLOYMENT_SIGNALS)

    if research_signals >= 2 and deployment_signals == 0:
        return 0.9
    return 0.0


def detect_recent_only_llm_wrapper(candidate):
    """
    Disqualifier #2: "AI experience" = recent (<12mo) LangChain+OpenAI
    wrapping, with no pre-LLM-era ML production background.
    """
    history = candidate.get("career_history", [])
    if not history:
        return 0.0

    try:
        sorted_hist = sorted(history, key=lambda r: r.get("start_date", ""))
    except Exception:
        sorted_hist = history

    total_months = sum(r.get("duration_months", 0) for r in sorted_hist)
    if total_months < 24:  # too junior for this disqualifier to apply meaningfully
        return 0.0

    ctext = career_text(candidate)
    has_wrapper_signal = text_contains_any(ctext, LANGCHAIN_WRAPPER_KEYWORDS) > 0
    has_pre_llm_ml = text_contains_any(ctext, PRE_LLM_ML_KEYWORDS) > 0

    # Recent role duration < 12mo AND is the *only* AI-relevant role
    recent_role = sorted_hist[-1] if sorted_hist else None
    recent_is_short_ai_role = (
        recent_role is not None
        and recent_role.get("duration_months", 999) < 12
        and text_contains_any(normalize(recent_role.get("description", "")), CAREER_ML_KEYWORDS) >= 2
    )

    if has_wrapper_signal and not has_pre_llm_ml and recent_is_short_ai_role:
        return 0.7
    return 0.0


def detect_architecture_drift(candidate):
    """Disqualifier #3: senior engineer, no production code in 18 months."""
    history = candidate.get("career_history", [])
    if not history:
        return 0.0
    try:
        sorted_hist = sorted(history, key=lambda r: r.get("start_date", ""))
    except Exception:
        sorted_hist = history

    current_role = next((r for r in sorted_hist if r.get("is_current")), sorted_hist[-1] if sorted_hist else None)
    if current_role is None:
        return 0.0

    title = normalize(current_role.get("title", ""))
    is_architect_role = any(kw in title for kw in ARCHITECT_DRIFT_TITLES)
    duration = current_role.get("duration_months", 0)

    if is_architect_role and duration >= 18:
        desc = normalize(current_role.get("description", ""))
        codes_hands_on = text_contains_any(desc, {"hands-on", "writes code", "ships code", "still codes"})
        if codes_hands_on == 0:
            return 0.6
    return 0.0


def detect_framework_enthusiast(candidate):
    """Disqualifier #5: GitHub full of tutorials, demo blog posts, no systems thinking."""
    ctext = career_text(candidate)
    summary = normalize(candidate["profile"].get("summary", "") + " " + candidate["profile"].get("headline", ""))
    full_text = ctext + " " + summary
    tutorial_hits = text_contains_any(full_text, FRAMEWORK_TUTORIAL_SIGNALS)
    if tutorial_hits >= 2:
        return 0.4
    return 0.0


def detect_cv_speech_without_ir(candidate):
    """Disqualifier #7."""
    skills_map = skill_set(candidate)
    has_cv = sum(1 for s in skills_map if any(cv in s for cv in CV_SPEECH_ROBOTICS_SKILLS)) >= 2
    has_ir = sum(1 for s in skills_map if any(ir in s for ir in IR_NLP_SKILLS)) >= 1
    if has_cv and not has_ir:
        return 0.5
    return 0.0


def detect_no_external_validation(candidate, yoe):
    """
    Disqualifier #8: 5+ years entirely closed-source, zero external validation.
    github_activity_score == -1 means no GitHub linked at all.
    """
    sig = candidate.get("redrob_signals", {})
    gh_score = sig.get("github_activity_score", -1)
    ctext = career_text(candidate)
    has_oss_mention = text_contains_any(ctext, {"open source", "open-source", "published", "talk at", "conference"})

    if yoe >= 5 and gh_score <= 0 and has_oss_mention == 0:
        return 0.35
    return 0.0


# ─── Scoring modules ──────────────────────────────────────────────────────────

def score_career(candidate):
    score = 0.0
    history = candidate.get("career_history", [])
    if not history:
        return 0.0

    profile = candidate["profile"]
    current_title = normalize(profile.get("current_title", ""))

    title_score = 0.25 if any(kw in current_title for kw in ML_TITLE_KEYWORDS) else (
        0.08 if any(t in current_title for t in ["engineer", "developer", "scientist"]) else 0.0
    )
    score += title_score

    ctext = career_text(candidate)
    ml_hits = text_contains_any(ctext, CAREER_ML_KEYWORDS)
    score += min(ml_hits / 10.0, 1.0) * 0.40

    if has_product_experience(candidate):
        score += 0.20

    current_company = normalize(profile.get("current_company", ""))
    if any(pc in current_company for pc in PRODUCT_COMPANIES):
        score += 0.05

    ml_titles_count = sum(
        1 for role in history
        if any(kw in normalize(role.get("title", "")) for kw in
               ["ml", "ai", "machine learning", "nlp", "engineer", "research", "applied"])
    )
    if ml_titles_count == 0:
        score *= 0.3

    if is_consulting_only(candidate):
        score *= 0.4

    return min(score, 1.0)


def score_skills(candidate):
    """Skills score now weighted by VERIFIED assessment scores where available,
    not just self-reported proficiency — addressing the JD's implicit warning
    that self-reported skill lists are gameable."""
    PROFICIENCY_WEIGHT = {"expert": 1.0, "advanced": 0.8, "intermediate": 0.5, "beginner": 0.2}
    skills_map = skill_set(candidate)
    assess = candidate["redrob_signals"].get("skill_assessment_scores", {}) or {}

    def effective_weight(skill_name, skill_data):
        pw = PROFICIENCY_WEIGHT.get(skill_data.get("proficiency", "beginner"), 0.2)
        raw_name = skill_data["name"]
        if raw_name in assess:
            # Blend self-reported with verified: verified gets 60% weight
            verified = assess[raw_name] / 100.0
            return 0.4 * pw + 0.6 * verified
        return pw

    must_have_weighted = 0.0
    must_have_matched = 0
    for skill_name, skill_data in skills_map.items():
        if any(mh in skill_name or skill_name in mh for mh in MUST_HAVE_SKILLS):
            must_have_weighted += effective_weight(skill_name, skill_data)
            must_have_matched += 1

    must_score = min(must_have_weighted / 5.0, 1.0) * 0.60

    nice_matched = sum(
        1 for skill_name in skills_map
        if any(nh in skill_name or skill_name in nh for nh in NICE_HAVE_SKILLS)
    )
    nice_score = min(nice_matched / 4.0, 1.0) * 0.25

    eval_matched = sum(
        1 for skill_name in skills_map
        if any(ev in skill_name for ev in EVAL_SKILLS)
    )
    eval_score = min(eval_matched / 2.0, 1.0) * 0.15

    total = must_score + nice_score + eval_score

    ctext = career_text(candidate)
    if must_have_matched >= 4 and text_contains_any(ctext, CAREER_ML_KEYWORDS) < 2:
        total *= 0.4  # keyword stuffer

    return min(total, 1.0)


def score_experience_band(candidate):
    yoe = candidate["profile"].get("years_of_experience", 0)
    if 5.0 <= yoe <= 9.0:
        return 1.0
    elif 4.0 <= yoe < 5.0 or 9.0 < yoe <= 10.0:
        return 0.80
    elif 3.0 <= yoe < 4.0 or 10.0 < yoe <= 12.0:
        return 0.55
    elif 2.0 <= yoe < 3.0:
        return 0.30
    else:
        return 0.15


def score_trajectory_integrity(candidate):
    """New module: rewards stable, deepening tenure; penalizes title-chasing."""
    penalty = detect_title_chasing(candidate)
    return max(0.0, 1.0 - penalty)


def score_behavioral(candidate):
    """Uses the full 23-signal redrob_signals object."""
    sig = candidate.get("redrob_signals", {})
    score = 0.0

    last_active_days = days_since(sig.get("last_active_date", "2020-01-01"))
    if last_active_days <= 14:
        recency = 1.0
    elif last_active_days <= 30:
        recency = 0.85
    elif last_active_days <= 60:
        recency = 0.65
    elif last_active_days <= 120:
        recency = 0.40
    elif last_active_days <= 180:
        recency = 0.20
    else:
        recency = 0.05
    score += recency * 0.22

    if sig.get("open_to_work_flag", False):
        score += 0.15

    rr = sig.get("recruiter_response_rate", 0)
    score += rr * 0.15

    # JD explicit: sub-30-day notice ideal, can buy out up to 30 days
    np_days = sig.get("notice_period_days", 90)
    if np_days <= 30:
        np_score = 1.0
    elif np_days <= 60:
        np_score = 0.70
    elif np_days <= 90:
        np_score = 0.45
    else:
        np_score = 0.15
    score += np_score * 0.15

    location = normalize(candidate["profile"].get("location", ""))
    country = normalize(candidate["profile"].get("country", ""))
    loc_text = location + " " + country
    if any(pl in loc_text for pl in PREFERRED_LOCATIONS):
        score += 0.10  # Pune/Noida explicit preference
    elif any(al in loc_text for al in ACCEPTABLE_LOCATIONS):
        score += 0.07
    elif sig.get("willing_to_relocate", False):
        score += 0.05

    # Interview completion rate — reliability signal
    icr = sig.get("interview_completion_rate", None)
    if icr is not None:
        score += icr * 0.08

    # Offer acceptance rate — -1 means no prior offers (neutral, not penalized)
    oar = sig.get("offer_acceptance_rate", -1)
    if oar is not None and oar >= 0:
        score += oar * 0.05

    completeness = sig.get("profile_completeness_score", 0) / 100.0
    score += completeness * 0.05

    # Verification signals (low weight, but matters for "can we reach them")
    verified = sum([
        sig.get("verified_email", False),
        sig.get("verified_phone", False),
        sig.get("linkedin_connected", False),
    ])
    score += (verified / 3.0) * 0.05

    return min(score, 1.0)


def score_external_validation(candidate, yoe):
    """github_activity_score as a 'show your thinking' proxy — JD explicitly
    values external validation (papers, talks, OSS) over closed-source-only work."""
    sig = candidate.get("redrob_signals", {})
    gh = sig.get("github_activity_score", -1)
    if gh < 0:
        return 0.3  # neutral-low: no GitHub linked, not necessarily disqualifying alone
    return min(gh / 100.0, 1.0)


def score_anti_patterns(candidate):
    """Composite penalty across all 8 JD-explicit disqualifiers."""
    yoe = candidate["profile"].get("years_of_experience", 0)
    penalty = 0.0

    penalty += detect_honeypot(candidate) * 0.35
    penalty += detect_research_only(candidate) * 0.45          # disqualifier #1
    penalty += detect_recent_only_llm_wrapper(candidate) * 0.40  # disqualifier #2
    penalty += detect_architecture_drift(candidate) * 0.35     # disqualifier #3
    penalty += detect_title_chasing(candidate) * 0.30          # disqualifier #4 (also in trajectory module)
    penalty += detect_framework_enthusiast(candidate) * 0.20   # disqualifier #5
    if is_consulting_only(candidate):                           # disqualifier #6
        penalty += 0.35
    penalty += detect_cv_speech_without_ir(candidate) * 0.30   # disqualifier #7
    penalty += detect_no_external_validation(candidate, yoe) * 0.15  # disqualifier #8

    current_title = normalize(candidate["profile"].get("current_title", ""))
    if any(t in current_title for t in NON_ML_TITLES):
        penalty += 0.35

    return min(penalty, 1.0)


# ─── Final composite scorer ───────────────────────────────────────────────────

WEIGHTS = {
    "career": 0.35,
    "skills": 0.25,
    "trajectory": 0.10,
    "experience": 0.10,
    "behavioral": 0.15,
    "external": 0.05,
}


def score_candidate(candidate):
    yoe = candidate["profile"].get("years_of_experience", 0)

    career = score_career(candidate)
    skills = score_skills(candidate)
    trajectory = score_trajectory_integrity(candidate)
    experience = score_experience_band(candidate)
    behavioral = score_behavioral(candidate)
    external = score_external_validation(candidate, yoe)
    anti = score_anti_patterns(candidate)

    raw = (
        career * WEIGHTS["career"]
        + skills * WEIGHTS["skills"]
        + trajectory * WEIGHTS["trajectory"]
        + experience * WEIGHTS["experience"]
        + behavioral * WEIGHTS["behavioral"]
        + external * WEIGHTS["external"]
    )

    penalty_multiplier = 1.0 - (anti * 0.85)
    final = raw * penalty_multiplier

    return round(final, 6), {
        "career": round(career, 3),
        "skills": round(skills, 3),
        "trajectory": round(trajectory, 3),
        "experience": round(experience, 3),
        "behavioral": round(behavioral, 3),
        "external": round(external, 3),
        "anti_penalty": round(anti, 3),
    }


# ─── Reasoning generator ─────────────────────────────────────────────────────

def generate_reasoning(candidate, components, rank):
    profile = candidate["profile"]
    sig = candidate.get("redrob_signals", {})
    title = profile.get("current_title", "")
    yoe = profile.get("years_of_experience", 0)
    company = profile.get("current_company", "")
    skills_map = skill_set(candidate)

    matched_musts = [
        s for s in skills_map
        if any(mh in s or s in mh for mh in MUST_HAVE_SKILLS)
    ][:3]

    rr = sig.get("recruiter_response_rate", 0)
    last_active = sig.get("last_active_date", "")
    days_ago = days_since(last_active)
    np_days = sig.get("notice_period_days", 0)

    parts = []

    if components["career"] >= 0.5 and components["skills"] >= 0.4:
        parts.append(
            f"{yoe:.1f}yr {title} at {company} with production "
            f"{'embedding/retrieval' if matched_musts else 'ML'} experience"
            + (f" ({', '.join(matched_musts[:2])})" if matched_musts else "")
        )
    elif components["career"] >= 0.3:
        parts.append(f"{yoe:.1f}yr {title} at {company}; partial fit")
    else:
        parts.append(f"{yoe:.1f}yr {title} at {company}; adjacent skills only")

    if components.get("anti_penalty", 0) >= 0.3:
        flags = []
        if detect_title_chasing(candidate) > 0:
            flags.append("title-chasing pattern")
        if detect_research_only(candidate) > 0:
            flags.append("research-only background")
        if detect_architecture_drift(candidate) > 0:
            flags.append("no recent hands-on code")
        if flags:
            parts.append("flags: " + ", ".join(flags))

    if rr >= 0.7 and days_ago <= 30:
        parts.append(f"highly engaged ({rr:.0%} response, active {days_ago}d ago)")
    elif rr < 0.2 or days_ago > 180:
        concern = []
        if days_ago > 180:
            concern.append(f"inactive {days_ago}d")
        if rr < 0.2:
            concern.append(f"low response ({rr:.0%})")
        parts.append("concern: " + ", ".join(concern))

    if rank <= 30 and np_days > 60:
        parts.append(f"notice {np_days}d may slow start")
    elif rank <= 30 and np_days <= 30:
        parts.append(f"short notice ({np_days}d)")

    return "; ".join(parts)[:300]


# ─── Main pipeline ────────────────────────────────────────────────────────────

def rank_candidates(candidates_path, out_path, top_n=100):
    print(f"Loading candidates from {candidates_path}...", flush=True)

    scored = []
    total = 0
    errors = 0

    with open(candidates_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                candidate = json.loads(line)
                final_score, components = score_candidate(candidate)
                scored.append((final_score, candidate, components))
                total += 1
                if total % 10000 == 0:
                    print(f"  Processed {total:,} candidates...", flush=True)
            except Exception:
                errors += 1

    print(f"Processed {total:,} candidates ({errors} errors).")

    # Sort on the ROUNDED score (what's actually displayed in the CSV) so that
    # candidates which round to the same value are tie-broken correctly by
    # candidate_id, matching what the validator checks against the file content.
    scored = [(round(s, 4), c, comp) for (s, c, comp) in scored]
    scored.sort(key=lambda x: (-x[0], x[1]["candidate_id"]))
    top100 = scored[:top_n]

    print(f"Writing top {top_n} to {out_path}...")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["candidate_id", "rank", "score", "reasoning"])
        for rank_idx, (score, candidate, components) in enumerate(top100, start=1):
            cid = candidate["candidate_id"]
            reasoning = generate_reasoning(candidate, components, rank_idx)
            writer.writerow([cid, rank_idx, f"{score:.4f}", reasoning])

    print(f"Done. Top score: {top100[0][0]:.4f}, rank-100 score: {top100[-1][0]:.4f}")

    print("\nTop 10 candidates:")
    print(f"{'Rank':>4} {'ID':<15} {'Score':>7} {'Title':<35} {'YoE':>5}")
    print("-" * 75)
    for rank_idx, (score, candidate, components) in enumerate(top100[:10], start=1):
        p = candidate["profile"]
        print(f"{rank_idx:>4} {candidate['candidate_id']:<15} {score:>7.4f} "
              f"{p['current_title'][:34]:<35} {p['years_of_experience']:>5.1f}")

    return top100


def main():
    parser = argparse.ArgumentParser(description="Redrob Candidate Ranker v2")
    parser.add_argument("--candidates", default="candidates.jsonl")
    parser.add_argument("--out", default="submission.csv")
    parser.add_argument("--top", type=int, default=100)
    args = parser.parse_args()

    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        print(f"ERROR: Candidates file not found: {candidates_path}", file=sys.stderr)
        sys.exit(1)

    rank_candidates(str(candidates_path), args.out, top_n=args.top)


if __name__ == "__main__":
    main()
