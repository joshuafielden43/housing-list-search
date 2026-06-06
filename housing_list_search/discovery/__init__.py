# discovery package
# run_first_discovery is the entry point used by cli.py --discover / --refresh-targets.
# Link discovery helpers live in link_discovery.py and scoring.py.

from .run import run_first_discovery

__all__ = ["run_first_discovery"]
