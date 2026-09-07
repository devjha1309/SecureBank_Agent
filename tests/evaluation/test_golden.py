import json
from pathlib import Path

import pytest

from evaluations.evaluators.run import evaluate

CASES = json.loads(Path("evaluations/datasets/golden.json").read_text())


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] != "runtime"], ids=lambda c: c["id"])
def test_golden(case):
    assert evaluate(case)["passed"], case


def test_corpus_size():
    assert len(CASES) >= 100
