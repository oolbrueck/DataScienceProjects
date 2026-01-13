from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_api import LlmClient


@dataclass
class RepoAnalysisResult:
    repo_full_name: str
    classification: Optional[Dict[str, Any]] = None
    explainability: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None


class RepoAnalyzer:
    def __init__(
        self,
        llm_client: LlmClient,
        schema_path: Optional[str] = None,
        template_path: Optional[str] = None,
        max_retries: int = 1,
    ) -> None:
        base_dir = Path(__file__).resolve().parent
        self.schema_path = Path(schema_path) if schema_path else base_dir / "n8n_readme_classification_schema.json"
        self.template_path = Path(template_path) if template_path else base_dir / "lllm_response_template.json"
        self.llm_client = llm_client
        self.max_retries = max_retries

        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self.template = json.loads(self.template_path.read_text(encoding="utf-8"))

    def analyze(
        self,
        repo_full_name: str,
        readme: str,
        file_tree: List[dict],
        high_signal_files: Optional[Dict[str, str]] = None,
    ) -> RepoAnalysisResult:
        prompt = self._build_prompt(readme, file_tree, high_signal_files or {})
        errors: List[str] = []

        for attempt in range(self.max_retries + 1):
            response_text = self.llm_client.generate(prompt)
            parsed = self._extract_json(response_text)
            if parsed is None:
                errors.append("Failed to parse JSON response")
            else:
                valid, validation_errors = self._validate_response(parsed)
                if valid:
                    return RepoAnalysisResult(
                        repo_full_name=repo_full_name,
                        classification=parsed.get("classification"),
                        explainability=parsed.get("explainability"),
                        raw=parsed,
                    )
                errors.extend(validation_errors)

            if attempt < self.max_retries:
                prompt = self._build_retry_prompt(errors, readme, file_tree, high_signal_files or {})

        return RepoAnalysisResult(
            repo_full_name=repo_full_name,
            raw=None,
            errors=errors,
        )

    def _build_prompt(
        self,
        readme: str,
        file_tree: List[dict],
        high_signal_files: Dict[str, str],
    ) -> str:
        file_tree_summary = self._summarize_file_tree(file_tree)
        high_signal_summary = {
            path: content[:4000] for path, content in high_signal_files.items()
        }
        prompt = (
            "You are classifying a GitHub repository that uses n8n. "
            "Use only the README and file structure to fill the schema. "
            "Return JSON only, matching the template exactly.\n\n"
            "Schema:\n"
            f"{json.dumps(self.schema, indent=2)}\n\n"
            "Response template:\n"
            f"{json.dumps(self.template, indent=2)}\n\n"
            "README:\n"
            f"{readme}\n\n"
            "File tree (top-level):\n"
            f"{json.dumps(file_tree_summary, indent=2)}\n\n"
            "High-signal files (truncated):\n"
            f"{json.dumps(high_signal_summary, indent=2)}\n"
        )
        return prompt

    def _build_retry_prompt(
        self,
        errors: List[str],
        readme: str,
        file_tree: List[dict],
        high_signal_files: Dict[str, str],
    ) -> str:
        error_text = "\n".join(f"- {err}" for err in errors[-10:])
        return (
            "Your previous response did not validate. Fix the JSON and try again. "
            "Only output corrected JSON, nothing else.\n"
            f"Validation errors:\n{error_text}\n\n"
            + self._build_prompt(readme, file_tree, high_signal_files)
        )

    def _summarize_file_tree(self, file_tree: List[dict]) -> List[Dict[str, str]]:
        summary: List[Dict[str, str]] = []
        for item in file_tree:
            path = item.get("path")
            entry_type = item.get("type")
            if path and entry_type:
                summary.append({"path": path, "type": entry_type})
        return summary

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        json_text = match.group(0)
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            return None

    def _validate_response(self, payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        classification = payload.get("classification")
        explainability = payload.get("explainability")
        if not isinstance(classification, dict):
            errors.append("Missing classification object")
        if not isinstance(explainability, dict):
            errors.append("Missing explainability object")
        if errors:
            return False, errors

        errors.extend(self._validate_classification(classification))
        errors.extend(self._validate_explainability(explainability))
        return len(errors) == 0, errors

    def _validate_classification(self, classification: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        schema_classification = self.schema.get("classification", {})

        errors.extend(self._validate_single_field(
            classification,
            ["project_context", "project_type"],
            schema_classification,
        ))
        errors.extend(self._validate_single_field(
            classification,
            ["project_context", "intended_usage"],
            schema_classification,
        ))
        errors.extend(self._validate_single_field(
            classification,
            ["business_orientation", "market_type"],
            schema_classification,
        ))
        errors.extend(self._validate_single_field(
            classification,
            ["business_orientation", "business_domain_language"],
            schema_classification,
        ))
        errors.extend(self._validate_single_field(
            classification,
            ["software_domain", "primary_domain"],
            schema_classification,
        ))
        errors.extend(self._validate_multi_field(
            classification,
            ["software_domain", "secondary_domains"],
            schema_classification,
        ))
        errors.extend(self._validate_single_field(
            classification,
            ["use_case_granularity", "granularity_level"],
            schema_classification,
        ))
        errors.extend(self._validate_free_text_list(
            classification,
            ["use_case_granularity", "described_use_cases"],
            schema_classification,
        ))
        errors.extend(self._validate_multi_field(
            classification,
            ["target_users", "primary_users"],
            schema_classification,
        ))

        return errors

    def _validate_explainability(self, explainability: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        schema_explainability = self.schema.get("explainability", {})

        errors.extend(self._validate_free_text_list(
            explainability,
            ["evidence_quotes"],
            schema_explainability,
        ))

        confidence = explainability.get("confidence_overall")
        allowed_range = schema_explainability.get("confidence_overall", {}).get("allowed_range", [])
        if not isinstance(confidence, (int, float)):
            errors.append("confidence_overall must be a number")
        elif len(allowed_range) == 2:
            low, high = allowed_range
            if confidence < low or confidence > high:
                errors.append("confidence_overall out of allowed range")

        uncertain = explainability.get("uncertain_fields")
        if not isinstance(uncertain, list):
            errors.append("uncertain_fields must be a list")

        return errors

    def _validate_single_field(
        self,
        classification: Dict[str, Any],
        field_path: List[str],
        schema_classification: Dict[str, Any],
    ) -> List[str]:
        errors: List[str] = []
        current = classification
        schema_current: Any = schema_classification
        for key in field_path:
            if not isinstance(current, dict) or key not in current:
                errors.append(f"Missing field {'.'.join(field_path)}")
                return errors
            current = current[key]
            schema_current = schema_current.get(key, {}) if isinstance(schema_current, dict) else {}

        allowed = schema_current.get("allowed_values")
        if isinstance(allowed, list) and current not in allowed:
            errors.append(f"Invalid value for {'.'.join(field_path)}")
        return errors

    def _validate_multi_field(
        self,
        classification: Dict[str, Any],
        field_path: List[str],
        schema_classification: Dict[str, Any],
    ) -> List[str]:
        errors: List[str] = []
        current = classification
        schema_current: Any = schema_classification
        for key in field_path:
            if not isinstance(current, dict) or key not in current:
                errors.append(f"Missing field {'.'.join(field_path)}")
                return errors
            current = current[key]
            schema_current = schema_current.get(key, {}) if isinstance(schema_current, dict) else {}

        if not isinstance(current, list):
            errors.append(f"Field {'.'.join(field_path)} must be a list")
            return errors

        allowed = schema_current.get("allowed_values")
        if isinstance(allowed, list):
            invalid = [item for item in current if item not in allowed]
            if invalid:
                errors.append(f"Invalid values for {'.'.join(field_path)}")
        return errors

    def _validate_free_text_list(
        self,
        root: Dict[str, Any],
        field_path: List[str],
        schema_root: Dict[str, Any],
    ) -> List[str]:
        errors: List[str] = []
        current = root
        schema_current: Any = schema_root
        for key in field_path:
            if not isinstance(current, dict) or key not in current:
                errors.append(f"Missing field {'.'.join(field_path)}")
                return errors
            current = current[key]
            schema_current = schema_current.get(key, {}) if isinstance(schema_current, dict) else {}

        if not isinstance(current, list):
            errors.append(f"Field {'.'.join(field_path)} must be a list")
            return errors

        max_items = schema_current.get("max_items")
        if isinstance(max_items, int) and len(current) > max_items:
            errors.append(f"Field {'.'.join(field_path)} exceeds max items")
        return errors
