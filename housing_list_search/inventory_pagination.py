"""
inventory_pagination.py — shared walk for multi-page inventory adapters (#1074 / #776).

Contract:
  fetch_page(page_num) → (items, more)
    items: records (or raw rows) from this 1-based page
    more: True if another page may exist (caller saw a full/non-empty page)

Stops when more is False. If max_pages is exhausted while more is still True,
raises SourceFetchError.pagination_cap so dispatch marks SCRAPE_FAILED (not
silent truncate / STALE).

Adapters supply only URL/API + parse; the cap semantics live here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from housing_list_search.access import SourceFetchError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# page_num is 1-based; returns (items_this_page, more_pages_may_exist)
FetchPage = Callable[[int], tuple[list[T], bool]]


def walk_paginated_inventory(
    *,
    adapter: str,
    max_pages: int,
    fetch_page: FetchPage[T],
) -> list[T]:
    """
    Walk pages until exhausted or safety cap.

    Raises SourceFetchError.pagination_cap when the cap is hit with more=True
    (full final page / exceededTransferLimit-style signal).
    """
    if max_pages < 1:
        raise ValueError(f"{adapter}: max_pages must be >= 1")

    collected: list[T] = []
    for page_num in range(1, max_pages + 1):
        items, more = fetch_page(page_num)
        if items:
            collected.extend(items)
        if not more:
            return collected
        # more=True with an empty page is a broken signal — stop rather than spin
        if not items:
            logger.warning(
                "%s: page %d signalled more=True with zero items — stopping",
                adapter,
                page_num,
            )
            return collected
        if page_num >= max_pages:
            logger.error(
                "%s: pagination hit max_pages=%d with more data "
                "(%d records so far)",
                adapter,
                max_pages,
                len(collected),
            )
            raise SourceFetchError.pagination_cap(
                adapter,
                max_pages=max_pages,
                partial=collected,
                detail=f"{len(collected)} records so far",
            )

    return collected
