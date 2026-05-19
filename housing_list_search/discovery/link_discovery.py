"""
Conservative link discovery for agentic housing list search.

This module is intentionally high-precision (Option A).
It is designed to propose good candidates for human review into TARGETS.md,
not to blindly auto-add everything.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get
from housing_list_search.discovery.scoring import (
    LinkCandidate,
    score_link,
    is_worth_considering,
)


logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Structured output from discovery."""
    start_url: str
    candidates: List[LinkCandidate]

    def to_markdown_table(self) -> str:
        """Human-readable markdown table for review."""
        if not self.candidates:
            return "No high-quality candidates found."

        lines = [
            "| Score | URL | Title | Suggested Category | Reason |",
            "|-------|-----|-------|--------------------|--------|",
        ]
        for c in sorted(self.candidates, key=lambda x: x.score, reverse=True):
            lines.append(
                f"| {c.score} | {c.url} | {c.text[:80]} | {c.suggested_category} | {c.reason[:60]}... |"
            )
        return "\n".join(lines)

    def to_structured_dicts(self) -> List[dict]:
        """Machine-friendly output (for future TARGETS.md merging, DB, etc.)."""
        return [asdict(c) for c in self.candidates]


def discover_links(
    start_url: str,
    max_links: int = 50,
    min_score: int = 40,
    conservative: bool = True,
) -> DiscoveryResult:
    """
    Conservative discovery from a broad starting page.

    Args:
        start_url: The broad housing/community services page (e.g. Gilroy /279/)
        max_links: Safety cap on how many links we even consider
        min_score: Minimum score required to be recommended (conservative default)
        conservative: When True, applies stricter filtering

    Returns:
        DiscoveryResult containing both structured objects and markdown table
    """
    logger.info(f"Starting conservative discovery from: {start_url}")

    resp = polite_get(start_url)
    if not resp:
        logger.warning(f"Failed to fetch start URL: {start_url}")
        return DiscoveryResult(start_url=start_url, candidates=[])

    soup = BeautifulSoup(resp.text, "lxml")

    candidates: List[LinkCandidate] = []
    seen_urls = set()

    # Extract all <a> tags
    for a_tag in soup.find_all("a", href=True)[:max_links]:
        href = a_tag["href"].strip()
        text = a_tag.get_text(" ", strip=True)

        if not href or href.startswith("#") or "javascript:" in href.lower():
            continue

        full_url = urljoin(start_url, href)

        # Skip external domains for the first version (can relax later)
        if urlparse(full_url).netloc not in urlparse(start_url).netloc:
            continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        candidate = score_link(full_url, text)

        # Conservative gate
        if is_worth_considering(candidate, min_score=min_score):
            candidates.append(candidate)

            # INFO level: just URL + title (as requested)
            logger.info(f"Proposed: {candidate.url} | {candidate.text[:100]}")

            # DEBUG level: full scoring details
            logger.debug(
                f"Score={candidate.score} | Category={candidate.suggested_category} | "
                f"Reason={candidate.reason} | URL={candidate.url}"
            )

    logger.info(f"Discovery complete. {len(candidates)} candidates passed conservative filter.")

    return DiscoveryResult(start_url=start_url, candidates=candidates)
