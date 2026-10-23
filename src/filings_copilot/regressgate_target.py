"""Entry point for `regressgate run` (see regressgate.yaml): the agent as a regressgate target.

The runtime is built once per process from the usual FILINGS_* settings and .env, so the
same suite runs locally and in the eval-gate workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any

from regressgate import TargetResult

from filings_copilot.app_factory import build_runtime
from filings_copilot.config import get_settings
from filings_copilot.evaluation import agent_target


@lru_cache(maxsize=1)
def _target() -> Callable[[Any], TargetResult]:
    return agent_target(build_runtime(get_settings()).agent)


def answer(question: Any) -> TargetResult:
    return _target()(question)
