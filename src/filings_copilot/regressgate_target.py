"""Entry point for `regressgate run` (see regressgate.yaml): the agent as a regressgate target.

The runtime is built once per process from the usual FILINGS_* settings and .env, so the
same suite runs locally and in the eval-gate workflow.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from regressgate import TargetResult

from filings_copilot.app_factory import build_runtime
from filings_copilot.config import get_settings
from filings_copilot.evaluation import agent_target

_lock = threading.Lock()
_target: Callable[[Any], TargetResult] | None = None


def _get_target() -> Callable[[Any], TargetResult]:
    # regressgate calls targets from several threads at once. Building the runtime twice
    # would open a second connection to the locked-down serving database, which DuckDB
    # rejects, so the first build must finish before anyone else uses the target.
    global _target
    with _lock:
        if _target is None:
            _target = agent_target(build_runtime(get_settings()).agent)
        return _target


def answer(question: Any) -> TargetResult:
    return _get_target()(question)
