"""Measure deterministic routing/guardrails and execute required runtime regressions."""

import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from agents.coordinator.graph import INTENT_TOOL
from core.errors import BankError
from core.guardrails.input import validate_message
from core.model_router.router import deterministic_plan
from core.pii.redaction import redact

ROOT = Path(__file__).resolve().parents[1]


def evaluate(case):
    if case["kind"] == "routing":
        plan = deterministic_plan(case["message"])
        tools = [INTENT_TOOL[i] for i in plan.intents if i in INTENT_TOOL]
        return {
            "passed": plan.intents == case["intents"] and tools == case["tools"],
            "intent": plan.intents == case["intents"],
            "tools": tools == case["tools"],
        }
    if case["kind"] == "injection":
        try:
            validate_message(case["message"])
        except BankError:
            return {"passed": True}
        return {"passed": False}
    if case["kind"] == "pii":
        return {"passed": case["message"] not in redact(case["message"])}
    return {"passed": None}


def main():
    cases = json.loads((ROOT / "datasets/golden.json").read_text())
    started = perf_counter()
    results = [{"id": c["id"], "kind": c["kind"], **evaluate(c)} for c in cases if c["kind"] != "runtime"]
    runtime = [c["pytest_test"] for c in cases if c["kind"] == "runtime"]
    # Runtime and security gates cannot be replaced with classifier-only scores.
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/security",
            "tests/integration",
            "-k",
            " or ".join(runtime) + " or authorization or confirmation",
            "--junitxml=evaluations/reports/runtime.xml",
        ]
    )
    route = [r for r in results if r["kind"] == "routing"]
    report = {
        "scenario_count": len(cases),
        "deterministic_cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "intent_accuracy": sum(r["intent"] for r in route) / len(route),
        "tool_selection_accuracy": sum(r["tools"] for r in route) / len(route),
        "pii_leakage_rate": sum(not r["passed"] for r in results if r["kind"] == "pii")
        / sum(r["kind"] == "pii" for r in results),
        "runtime_gates_passed": process.returncode == 0,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "mock_model_cost_usd": 0,
        "limitations": "Routing metrics cover this synthetic corpus only. Runtime assertions cover grounding, authorization, confirmation and idempotency; production completion and external model cost require deployed measurements.",
        "results": results,
    }
    (ROOT / "reports/latest.json").write_text(json.dumps(report, indent=2))
    if "--persist" in sys.argv:
        from mock_bank.database.session import SessionLocal
        from mock_bank.models.entities import EvaluationResult

        with SessionLocal.begin() as db:
            for result in results:
                db.add(
                    EvaluationResult(
                        scenario=result["id"],
                        passed=result["passed"],
                        metrics={k: v for k, v in result.items() if k not in {"id", "passed"}},
                    )
                )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    if process.returncode or not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
