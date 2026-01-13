from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests


@dataclass
class RepoSnapshot:
    owner: str
    repo: str
    readme: str
    file_tree: List[dict]
    high_signal_files: Dict[str, str]


class GitHubApiError(RuntimeError):
    pass


class RepoContentExtractor:
    BASE_URL = "https://api.github.com"

    RX_HIGH_SIGNAL = re.compile(
        r"(docker-compose\.ya?ml|deployment\.ya?ml|statefulset\.ya?ml|kustomization\.ya?ml|"
        r"Chart\.yaml|values\.ya?ml|\.env(\.example)?|nginx\.conf|Caddyfile|"
        r"traefik\.ya?ml|dockerfile)$",
        re.IGNORECASE,
    )

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

    def fetch_snapshot(self, owner: str, repo: str) -> RepoSnapshot:
        file_tree = self._list_dir(owner, repo, "")
        readme = self._get_readme(owner, repo) or ""
        high_signal_files: Dict[str, str] = {}

        for item in file_tree:
            if item.get("type") != "file":
                continue
            path = item.get("path", "")
            if not path or not self.RX_HIGH_SIGNAL.search(path):
                continue
            content = self._get_text_file(owner, repo, path)
            if content:
                high_signal_files[path] = content

        return RepoSnapshot(
            owner=owner,
            repo=repo,
            readme=readme,
            file_tree=file_tree,
            high_signal_files=high_signal_files,
        )

    def _list_dir(self, owner: str, repo: str, path: str) -> List[dict]:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}".rstrip("/")
        data = self._request_json("GET", url)
        if isinstance(data, list):
            return data
        return []

    def _get_readme(self, owner: str, repo: str) -> Optional[str]:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/readme"
        try:
            data = self._request_json("GET", url)
            return self._decode_content(data)
        except Exception:
            return None

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

        if isinstance(size, int) and size > 500_000:
            return None

        if content and encoding == "base64":
            raw = base64.b64decode(content.encode("utf-8"))
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

                if resp.status_code in (403, 429):
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    if remaining == "0" and reset:
                        sleep_s = max(1, int(reset) - int(time.time()) + 2)
                        time.sleep(sleep_s)
                        continue
                    time.sleep(20)
                    continue

                if resp.status_code >= 400:
                    raise GitHubApiError(f"GitHub API error {resp.status_code}: {resp.text[:300]}")

                return resp.json()

            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 15))

        raise GitHubApiError(f"Failed after retries. Last error: {last_err}")
