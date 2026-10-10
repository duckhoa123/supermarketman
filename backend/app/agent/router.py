"""Pick the model for a question: cheap by default, stronger only when the question needs analysis."""

import re

_DEEP_PATTERNS = [
    r"\bwhy\b",
    r"\bforecast",
    r"\bpredict",
    r"\bproject(ion|ed)?\b",
    r"\bplan\b",
    r"\bstrateg",
    r"\brecommend",
    r"\bshould i\b",
    r"\bwhat if\b",
    r"\boptimi[sz]e",
    r"\banaly[sz]",
    r"\btrend",
    r"\bcompare\b.*\b(and|vs|versus)\b.*\b(and|vs|versus)\b",  # three or more things compared
    r"\bmenu engineering\b",
    r"\bpricing\b",
]
_DEEP_RE = re.compile("|".join(_DEEP_PATTERNS), re.IGNORECASE)


def needs_deep_model(question: str) -> bool:
    if len(question) > 600:
        return True
    return bool(_DEEP_RE.search(question))


def choose_model(
    question: str, *, deep_requested: bool, budget_exceeded: bool, default_model: str, deep_model: str
) -> str:
    if budget_exceeded:
        return default_model
    if deep_requested or needs_deep_model(question):
        return deep_model
    return default_model
