from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from filings_copilot import regressgate_target
from filings_copilot.research_agent import AgentResult


class FakeAgent:
    model = "fake"

    def ask(self, question: str) -> AgentResult:
        return AgentResult(answer=f"answer to {question}", citations=[], tool_calls=[])


def test_concurrent_first_calls_build_the_runtime_once(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0

    def slow_build(settings: Any) -> SimpleNamespace:
        nonlocal builds
        builds += 1
        time.sleep(0.05)  # wide window for a race
        return SimpleNamespace(agent=FakeAgent())

    monkeypatch.setattr(regressgate_target, "build_runtime", slow_build)
    monkeypatch.setattr(regressgate_target, "_target", None)
    outputs: list[str] = []
    threads = [threading.Thread(target=lambda i=i: outputs.append(
        regressgate_target.answer(f"q{i}").output)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert builds == 1
    assert sorted(outputs) == [f"answer to q{i}" for i in range(4)]
