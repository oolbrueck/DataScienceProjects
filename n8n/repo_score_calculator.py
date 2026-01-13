# repo_score_calculator.py
from __future__ import annotations

import base64
import os
import re
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass
class RepoScore:
    total: int
    usage_score: int
    plugin_score: int
    features: Dict[str, int]
    evidence: Dict[str, List[str]]  # small snippets / file hits


class GitHubApiError(RuntimeError):
    pass


class RepoScoreCalculator:
    """
    Computes a heuristic "usage vs plugin" score for a repo.

    High-signal "usage" indicators:
      - docker-compose.yml contains n8n image references
      - k8s/helm manifests reference n8n image
      - env vars: N8N_HOST, WEBHOOK_URL, N8N_ENCRYPTION_KEY, etc.

    Plugin indicators:
      - repo name matches n8n-nodes-* patterns
      - package.json contains n8n-core / n8n-workflow / @n8n/node-dev

    Strategy:
      - Fetch minimal content via GitHub Contents API (README + selected files)
      - Apply regex matches, feature weights, and return RepoScore
    """

    BASE_URL = "https://api.github.com"

    # Strong usage signals
    RX_N8N_IMAGE = re.compile(r"(n8nio/n8n|n8n\.io/n8n)", re.IGNORECASE)
    RX_N8N_ENV = re.compile(r"\b(N8N_HOST|WEBHOOK_URL|N8N_ENCRYPTION_KEY|N8N_PORT|N8N_PROTOCOL|N8N_EDITOR_BASE_URL)\b")
    RX_DOCKER_COMPOSE = re.compile(r"docker-compose\.ya?ml", re.IGNORECASE)
    RX_K8S_FILE = re.compile(r"(deployment|statefulset|kustomization)\.ya?ml$", re.IGNORECASE)
    RX_HELM_FILE = re.compile(r"(Chart\.yaml|values\.ya?ml)$", re.IGNORECASE)

    # Plugin signals
    RX_PLUGIN_REPO_NAME = re.compile(r"^(n8n-(community-)?nodes?-|n8n-node-)", re.IGNORECASE)
    RX_PLUGIN_DEPS = re.compile(r'"(n8n-core|n8n-workflow|n8n-nodes-base|@n8n/node-dev|n8n-node-dev)"')

    def __init__(
        self,
        token: Optional[str] = None,
        user_agent: str = "n8n-research/1.0",
        timeout_s: int = 30,
        max_retries: int = 5,
    ) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.timeout_s = timeout_s
        self.max_retries = max_retries

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": user_agent,
            }
        )
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def score_repo(self, owner: str, repo: str) -> RepoScore:
        full_name = f"{owner}/{repo}"

        features: Dict[str, int] = {}
        evidence: Dict[str, List[str]] = {"hits": []}

        usage = 0
        plugin = 0

        # Repo-name plugin heuristics
        if self.RX_PLUGIN_REPO_NAME.search(repo):
            plugin += 5
            features["plugin_repo_name"] = 5
            evidence["hits"].append(f"{full_name}: repo name looks like plugin")

        # Fetch repo tree (top-level) to find candidate files cheaply
        root_listing = self._list_dir(owner, repo, "")
        top_paths = [item["path"] for item in root_listing if "path" in item]

        # Candidate files to fetch
        candidate_files = self._select_candidate_files(root_listing)

        # Always fetch README if present
        readme_text = self._get_readme(owner, repo)
        if readme_text:
            u, p, feats, ev = self._score_text("README", readme_text)
            usage += u
            plugin += p
            self._merge(features, feats)
            evidence["hits"].extend(ev)

        # Fetch candidates and score their contents
        for path in candidate_files:
            text = self._get_text_file(owner, repo, path)
            if not text:
                continue
            u, p, feats, ev = self._score_text(path, text)
            usage += u
            plugin += p
            self._merge(features, feats)
            evidence["hits"].extend(ev)

        total = usage - plugin
        return RepoScore(
            total=total,
            usage_score=usage,
            plugin_score=plugin,
            features=features,
            evidence=evidence,
        )

    # ---------- Scoring internals ----------

    def _score_text(self, label: str, text: str) -> Tuple[int, int, Dict[str, int], List[str]]:
        usage = 0
        plugin = 0
        feats: Dict[str, int] = {}
        ev: List[str] = []

        # Usage indicators
        if self.RX_N8N_IMAGE.search(text):
            usage += 5
            feats["n8n_image_ref"] = feats.get("n8n_image_ref", 0) + 5
            ev.append(f"{label}: contains n8n image reference")

        env_hits = set(self.RX_N8N_ENV.findall(text))
        if env_hits:
            pts = 3
            usage += pts
            feats["n8n_env_vars"] = feats.get("n8n_env_vars", 0) + pts
            ev.append(f"{label}: contains env vars {sorted(env_hits)[:5]}")

        # Plugin indicators via dependencies
        if self.RX_PLUGIN_DEPS.search(text):
            plugin += 4
            feats["plugin_deps"] = feats.get("plugin_deps", 0) + 4
            ev.append(f"{label}: contains plugin-like dependencies")

        return usage, plugin, feats, ev

    def _select_candidate_files(self, listing: List[dict]) -> List[str]:
        """
        Pick high-signal config files to fetch.
        Only fetch small subset to keep API calls manageable.
        """
        candidates: List[str] = []
        for item in listing:
            if item.get("type") != "file":
                continue
            path = item.get("path", "")
            name = item.get("name", "")

            if self.RX_DOCKER_COMPOSE.search(name):
                candidates.append(path)
                continue
            if self.RX_HELM_FILE.search(name):
                candidates.append(path)
                continue
            if name.lower() in ("package.json", ".env", ".env.example", "dockerfile"):
                candidates.append(path)
                continue
            if self.RX_K8S_FILE.search(name):
                candidates.append(path)
                continue

        # keep bounded
        return candidates[:15]

    def _merge(self, dst: Dict[str, int], src: Dict[str, int]) -> None:
        for k, v in src.items():
            dst[k] = dst.get(k, 0) + v

    # ---------- GitHub content API ----------

    def _get_readme(self, owner: str, repo: str) -> Optional[str]:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/readme"
        try:
            data = self._request_json("GET", url)
            return self._decode_content(data)
        except Exception:
            return None

    def _list_dir(self, owner: str, repo: str, path: str) -> List[dict]:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}".rstrip("/")
        data = self._request_json("GET", url)
        if isinstance(data, list):
            return data
        return []

    def _get_text_file(self, owner: str, repo: str, path: str) -> Optional[str]:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        try:
            data = self._request_json("GET", url)
            return self._decode_content(data)
        except Exception:
            return None

    def _decode_content(self, data: dict) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        content = data.get("content")
        encoding = data.get("encoding")
        size = data.get("size", 0)

        # Avoid huge files
        if isinstance(size, int) and size > 500_000:
            return None

        if content and encoding == "base64":
            raw = base64.b64decode(content.encode("utf-8"))
            # best-effort decode
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return raw.decode("latin-1", errors="replace")
        return None

    def _request_json(self, method: str, url: str, params: Optional[dict] = None, **kwargs) -> dict:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.request(method, url, params=params, timeout=self.timeout_s, **kwargs)

                # Handle rate limit
                if resp.status_code in (403, 429):
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    if remaining == "0" and reset:
                        sleep_s = max(1, int(reset) - int(time.time()) + 2)
                        logger.warning("Rate limit hit. Sleeping %ss", sleep_s)
                        time.sleep(sleep_s)
                        continue
                    # Secondary rate limit
                    logger.warning("Secondary rate limit. Sleeping 20s. %s", resp.text[:200])
                    time.sleep(20)
                    continue

                if resp.status_code >= 400:
                    raise GitHubApiError(f"GitHub API error {resp.status_code}: {resp.text[:300]}")

                return resp.json()

            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 15))

        raise GitHubApiError(f"Failed after retries. Last error: {last_err}")
