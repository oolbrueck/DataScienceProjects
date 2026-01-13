# repo_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class RepoAnalysisResult:
    """
    Placeholder for later LLM-based analysis output.
    """
    repo_full_name: str
    category: Optional[str] = None
    subcategory: Optional[str] = None
    n8n_role: Optional[str] = None  # e.g. "core orchestrator", "integration glue", "peripheral"
    notes: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class RepoAnalyzer:
    """
    Intentionally empty for now.

    Next step (later):
      - Take README + file list (+ optionally workflow JSON names)
      - Send compact prompt to an LLM
      - Return structured labels (category, n8n role, etc.)
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def analyze(self, repo_full_name: str, readme: str, file_tree: Dict[str, Any]) -> RepoAnalysisResult:
        # TODO: implement LLM-based classification
        return RepoAnalysisResult(repo_full_name=repo_full_name)
