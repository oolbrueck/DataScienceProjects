from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from llm_api import OpenAIChatClient
from possible_repo_retriever import PossibleRepoRetriever
from repo_analyzer import RepoAnalyzer
from repo_content_extractor import RepoContentExtractor
from repo_score_calculator import RepoScoreCalculator


DEFAULT_SCORE_THRESHOLD = 3


def filter_usage_repos(repos: List[str], scores: dict, threshold: int) -> List[str]:
    return [repo for repo in repos if scores.get(repo, 0) >= threshold]


def run_pipeline(
    output_path: Path,
    score_threshold: int = DEFAULT_SCORE_THRESHOLD,
    max_repos: int = 25,
) -> None:
    retriever = PossibleRepoRetriever()
    scorer = RepoScoreCalculator()
    extractor = RepoContentExtractor()
    analyzer = RepoAnalyzer(llm_client=OpenAIChatClient())

    candidates = retriever.retrieve_possible_repos()
    if max_repos:
        candidates = candidates[:max_repos]

    scores = {}
    for ref in candidates:
        score = scorer.score_repo(ref.owner, ref.name)
        scores[ref.full_name] = score.total

    usage_repos = filter_usage_repos([ref.full_name for ref in candidates], scores, score_threshold)

    results = []
    for full_name in usage_repos:
        owner, name = full_name.split("/", 1)
        snapshot = extractor.fetch_snapshot(owner, name)
        analysis = analyzer.analyze(
            repo_full_name=full_name,
            readme=snapshot.readme,
            file_tree=snapshot.file_tree,
            high_signal_files=snapshot.high_signal_files,
        )
        results.append(analysis)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(asdict(result), ensure_ascii=False))
            handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run n8n usage analysis pipeline")
    parser.add_argument("--output", default="n8n_results.jsonl", help="Output JSONL path")
    parser.add_argument("--score-threshold", type=int, default=DEFAULT_SCORE_THRESHOLD)
    parser.add_argument("--max-repos", type=int, default=25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline(
        output_path=Path(args.output),
        score_threshold=args.score_threshold,
        max_repos=args.max_repos,
    )


if __name__ == "__main__":
    main()
