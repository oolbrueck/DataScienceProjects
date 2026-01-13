# possible_repo_retriever.py
from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubApiError(RuntimeError):
    pass


class PossibleRepoRetriever:
    """
    Finds likely *usage/deployment* repos for n8n by using GitHub's Search Code API.

    Design:
      - Focus on "Search group 1" (deployment artifacts): docker-compose / k8s / helm / env vars.
      - Use multiple targeted search queries and union results.
      - Return a de-duplicated set of repos.

    Requirements:
      - Provide a GitHub token (recommended) via env var GITHUB_TOKEN or pass explicitly.
      - Search API has result caps (generally 1000 results per query). We mitigate by:
          * multiple narrower queries
          * optional time slicing via pushed ranges

    Notes:
      - You may want to tune queries over time based on actual hit quality.
      - This retriever is intentionally conservative (high precision).
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        user_agent: str = "n8n-research/1.0",
        timeout_s: int = 30,
        max_retries: int = 5,
        sleep_on_secondary_rate_limit_s: int = 30,
    ) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.sleep_on_secondary_rate_limit_s = sleep_on_secondary_rate_limit_s

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": user_agent,
            }
        )
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    # ---------- Public API ----------

    def retrieve_possible_repos(
        self,
        pushed_slices: Optional[List[str]] = None,
        per_page: int = 100,
        max_pages_per_query: int = 10,
    ) -> List[RepoRef]:
        """
        Retrieve candidate repos.

        Args:
          pushed_slices:
            Optional list of GitHub search qualifiers to time-slice results and avoid the 1000-cap.
            Example: ["pushed:2025-01-01..2025-03-31", "pushed:2025-04-01..2025-06-30", ...]
            If None, no slicing is applied.
          per_page:
            Items per page for Search API (max 100).
          max_pages_per_query:
            Cap pages to avoid excessive calls. With per_page=100, 10 pages = 1000 results max.

        Returns:
          List of RepoRef, de-duplicated.
        """
        queries = self._build_deployment_queries()

        all_repos: Dict[str, RepoRef] = {}
        slices = pushed_slices or [""]  # single empty slice = no qualifier

        for base_q in queries:
            for sl in slices:
                q = base_q if not sl else f"{base_q} {sl}".strip()
                logger.info("Searching: %s", q)
                for repo in self._search_code_repos(
                    q=q,
                    per_page=per_page,
                    max_pages=max_pages_per_query,
                ):
                    all_repos[repo.full_name] = repo

        return sorted(all_repos.values(), key=lambda r: r.full_name.lower())

    # ---------- Query design ----------

    def _build_deployment_queries(self) -> List[str]:
        """
        High-precision queries for n8n deployment usage.

        We intentionally avoid generic "n8n" string matches to reduce plugin/docs noise.
        """
        queries = [
            # docker-compose based deployments
            '("n8nio/n8n" OR "n8n.io/n8n") filename:docker-compose.yml',
            '("image: n8nio/n8n" OR "image: n8n.io/n8n") filename:docker-compose.yml',
            # common env vars used in n8n deployments
            '("N8N_HOST" OR "N8N_ENCRYPTION_KEY" OR "WEBHOOK_URL") (filename:.env OR filename:.env.example OR filename:docker-compose.yml)',
            # kubernetes manifests (not perfect, but tends to be usage)
            '("n8nio/n8n" OR "n8n.io/n8n") (filename:deployment.yaml OR filename:deployment.yml OR filename:kustomization.yaml OR filename:kustomization.yml)',
            # helm charts (templates often contain deployments)
            '("n8nio/n8n" OR "n8n.io/n8n") (path:charts OR path:helm OR filename:Chart.yaml)',
            # reverse proxy configs referencing webhook/base URL
            '("WEBHOOK_URL" OR "N8N_HOST") (filename:nginx.conf OR filename:Caddyfile OR filename:traefik.yml OR filename:traefik.yaml)',
        ]
        return queries

    # ---------- GitHub API helpers ----------

    def _search_code_repos(
        self,
        q: str,
        per_page: int,
        max_pages: int,
    ) -> Iterator[RepoRef]:
        """
        Search code and yield repositories (dedupe within query).
        """
        seen: Set[str] = set()
        for page in range(1, max_pages + 1):
            params = {"q": q, "per_page": per_page, "page": page}
            data = self._request_json("GET", f"{self.BASE_URL}/search/code", params=params)

            items = data.get("items", [])
            if not items:
                break

            for it in items:
                repo = it.get("repository") or {}
                full_name = repo.get("full_name")
                if not full_name or full_name in seen:
                    continue
                seen.add(full_name)
                owner, name = full_name.split("/", 1)
                yield RepoRef(owner=owner, name=name)

            # If fewer than per_page returned, likely last page
            if len(items) < per_page:
                break

    def _request_json(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """
        GitHub request wrapper with basic rate-limit + retry handling.
        """
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    timeout=self.timeout_s,
                    **kwargs,
                )

                # Rate limits
                if resp.status_code in (403, 429):
                    # Secondary rate limit or abuse detection sometimes returns 403 with message.
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    msg = ""
                    try:
                        msg = resp.json().get("message", "")
                    except Exception:
                        msg = resp.text[:200]

                    if remaining == "0" and reset:
                        sleep_s = max(1, int(reset) - int(time.time()) + 2)
                        logger.warning("Rate limit hit. Sleeping %ss. Message=%s", sleep_s, msg)
                        time.sleep(sleep_s)
                        continue

                    # Secondary rate limit: back off
                    logger.warning(
                        "Secondary/abuse rate limit? status=%s msg=%s; sleeping %ss",
                        resp.status_code,
                        msg,
                        self.sleep_on_secondary_rate_limit_s,
                    )
                    time.sleep(self.sleep_on_secondary_rate_limit_s)
                    continue

                if resp.status_code >= 400:
                    raise GitHubApiError(f"GitHub API error {resp.status_code}: {resp.text[:500]}")

                return resp.json()

            except Exception as e:
                last_err = e
                sleep_s = min(2 ** attempt, 20)
                logger.warning("Request failed (attempt %s/%s): %s. Sleeping %ss", attempt, self.max_retries, e, sleep_s)
                time.sleep(sleep_s)

        raise GitHubApiError(f"Failed after retries. Last error: {last_err}")
