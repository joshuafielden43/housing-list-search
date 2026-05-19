#!/usr/bin/env python3
"""
Thin launcher for the Housing List Aggregator.

This keeps `python main.py` working from the repo root while the real
implementation lives in the importable package.
"""

from housing_list_search.cli import main

if __name__ == "__main__":
    main()
