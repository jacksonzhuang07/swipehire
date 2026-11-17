"""Reusable answer bank: question normalization + fuzzy lookup.

A question answered once (by the user through the app) is stored against a
NORMALIZED form of the question so future variants of the same question —
"Are you authorized to work in Canada?" vs "Legally entitled to work
here?" — resolve to the same banked answer. The extension asks the server
for an answer by normalized key before parking an application.

Truthfulness invariant: banked answers come ONLY from user-supplied
facts/answers. Nothing in this module invents an answer.

The JS mirror of normalize_question lives in extension/content.js
(swipeNormalizeQuestion) and MUST stay in lockstep with this file.
"""
import re

# Phrase-level synonym maps, applied to the lowercased question BEFORE
# tokenizing. Each maps a family of phrasings onto one canonical token.
_SYNONYMS = [
    (r"legally (authorized|authorised|entitled|eligible) to work|authorized to work|authorised to work|eligible to work|right to work|work authorization|work authorisation|permission to work", "workauth"),
    (r"require sponsorship|need sponsorship|sponsorship (to work|now or in the future)|visa sponsorship|require a work (visa|permit)|need a work (visa|permit)", "sponsorship"),
    (r"salary expectation|expected salary|salary requirements|desired salary|compensation expectation|expected compensation|pay expectation", "salaryexpectation"),
    (r"years of experience|how many years of .* experience|years experience", "yearsexperience"),
    (r"when can you start|available to start|earliest start date|start date availability", "startdate"),
    (r"willing to relocate|open to relocation|able to relocate", "relocation"),
    (r"driver'?s licen[cs]e|driving licen[cs]e", "driverslicense"),
    (r"criminal record|background check|criminal background", "backgroundcheck"),
    (r"how did you hear about (us|this (job|role|position))|how did you find (us|this (job|role|position))", "referralsource"),
]

_STOPWORDS = {
    "are", "you", "do", "does", "the", "a", "an", "in", "to", "for", "of",
    "or", "and", "is", "it", "if", "so", "what", "whats", "please",
    "currently", "will", "would", "can", "could", "have", "has", "your",
    "yours", "this", "that", "with", "without", "any", "now", "later",
    "require", "required", "need", "needs", "us", "we", "our", "be", "been",
    "at", "on", "by", "from", "as", "about", "into", "over", "under",
    # Place words: "authorized to work in Canada" and "legally entitled
    # to work here" are the same question for a personal answer bank.
    "canada", "canadian", "here", "country", "province", "state",
}


def normalize_question(text):
    """Normalize a form question to its bank key.

    lowercase -> synonym phrases -> strip punctuation -> drop stopwords ->
    sorted unique tokens joined by single spaces.
    """
    s = (text or "").lower()
    for pat, repl in _SYNONYMS:
        s = re.sub(pat, f" {repl} ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = {t for t in s.split() if t and t not in _STOPWORDS}
    # A canonical family token IS the key: "Will you now or in the future
    # require sponsorship?" and "Do you need sponsorship to work?" are one
    # question, stray tokens ("future", "work") notwithstanding.
    for _pat, repl in _SYNONYMS:
        if repl in toks:
            return repl
    return " ".join(sorted(toks))


def _token_set(key):
    return set((key or "").split())


def lookup_answer(conn, candidate_id, question):
    """Return the banked answer for `question`, or None.

    Exact normalized-key match first; otherwise the best token-set
    Jaccard match at >= 0.6 against this candidate's bank.
    """
    key = normalize_question(question)
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM answer_bank WHERE candidate_id=? AND norm_key=?",
        (candidate_id, key)).fetchone()
    if row:
        return dict(row)
    want = _token_set(key)
    best, best_score = None, 0.0
    for r in conn.execute("SELECT * FROM answer_bank WHERE candidate_id=?",
                          (candidate_id,)).fetchall():
        have = _token_set(r["norm_key"])
        if not have:
            continue
        score = len(want & have) / float(len(want | have))
        if score > best_score:
            best, best_score = dict(r), score
    if best and best_score >= 0.6:
        return best
    return None


def bank_answer(conn, candidate_id, question, answer, source="user"):
    """Store (or replace) a user-supplied answer under its normalized key."""
    key = normalize_question(question)
    if not key or not (answer or "").strip():
        return None
    conn.execute(
        "INSERT INTO answer_bank(candidate_id,norm_key,question,answer,source) VALUES(?,?,?,?,?) "
        "ON CONFLICT(candidate_id,norm_key) DO UPDATE SET question=excluded.question, "
        "answer=excluded.answer, source=excluded.source, updated_at=datetime('now')",
        (candidate_id, key, (question or "")[:300], str(answer)[:500], source))
    conn.commit()
    return conn.execute(
        "SELECT * FROM answer_bank WHERE candidate_id=? AND norm_key=?",
        (candidate_id, key)).fetchone()
