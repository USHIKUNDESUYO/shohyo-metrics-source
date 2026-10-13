#!/usr/bin/env python3
"""run / match result JSON から評価指標を layer ごとに抽出する。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path


EXPECTED_DOC_TYPES = [
    "invoice",
    "tax_exempt_invoice",
    "professional",
    "receipt",
    "register_receipt",
    "statement",
]
DEFAULT_ACCURACY_TARGET = 0.999


def load_result(path: str | Path) -> dict:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"JSONに数値ではない定数{value}があります")
    def unique_fields(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError(f"JSONの項目名 {key!r} が同じオブジェクト内で重複しています。1つに直してください")
            values[key] = value
        return values
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid_constant,
                      object_pairs_hook=unique_fields)


def result_kind(result: dict) -> str:
    summary = result.get("summary", {})
    if "match_accuracy" in summary:
        return "matching"
    return "extraction"


def valid_number(value: object, *, maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # Compare integer values before isfinite to avoid overflowing Python's float conversion.
    if value < 0 or (maximum is not None and value > maximum):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_result(result: dict) -> None:
    if not isinstance(result, dict):
        raise ValueError("result JSON はオブジェクトにしてください")
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("result JSON に summary object がありません")
    results = result.get("results")
    if results is not None and not isinstance(results, list):
        raise ValueError("result JSON の results は list である必要があります")
    if "cases" not in summary and results is None:
        raise ValueError("result JSON に cases または results がありません")
    for key in ["cases", "parse_failures"]:
        value = summary.get(key)
        if key not in summary or (key == "parse_failures" and value is None):
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"summary.{key} は0以上の整数にしてください")
    for key in ["cases_by_type", "field_accuracy", "accuracy_by_doc_type", "run_config"]:
        value = summary.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"summary.{key} はオブジェクトにしてください")
    for key, value in (summary.get("cases_by_type") or {}).items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("summary.cases_by_type は文字列の種類名と0以上の整数の件数にしてください")
    for key in ["match_accuracy", "headline_billed_total_accuracy"]:
        value = summary.get(key)
        if value is not None and not valid_number(value, maximum=1):
            raise ValueError(f"summary.{key} は0以上1以下の有限の数値にしてください")
    for field, value in (summary.get("field_accuracy") or {}).items():
        if not valid_number(value, maximum=1):
            raise ValueError(f"summary.field_accuracy.{field} は0以上1以下の有限の数値にしてください")
    for key in ["avg_elapsed_sec", "max_elapsed_sec"]:
        value = summary.get(key)
        if value is not None and not valid_number(value):
            raise ValueError(f"summary.{key} は0以上の有限の数値にしてください")
    for i, row in enumerate(results or [], 1):
        prefix = f"resultsの{i}件目"
        if not isinstance(row, dict):
            raise ValueError(f"{prefix} はオブジェクトにしてください")
        for key in ["doc_type", "method", "file", "status"]:
            if row.get(key) is not None and not isinstance(row[key], str):
                raise ValueError(f"{prefix}の{key} は文字列にしてください")
        if "correct" in row and not isinstance(row["correct"], bool):
            raise ValueError(f"{prefix}のcorrect はJSONのtrueまたはfalseにしてください")
        match = row.get("match")
        if match is not None and not isinstance(match, dict):
            raise ValueError(f"{prefix}のmatch はオブジェクトにしてください")
        for field, value in (match or {}).items():
            if not isinstance(value, bool):
                raise ValueError(f"{prefix}のmatch.{field} はJSONのtrueまたはfalseにしてください")
        if row.get("elapsed_sec") is not None and not valid_number(row["elapsed_sec"]):
            raise ValueError(f"{prefix}のelapsed_sec は0以上の有限の数値にしてください")


def dataset_layer(summary: dict, results: list[dict]) -> dict:
    cases_by_type = summary.get("cases_by_type") or {}
    if not cases_by_type:
        inferred = Counter(row.get("doc_type") for row in results if row.get("doc_type"))
        cases_by_type = dict(inferred)
    present = sorted(doc_type for doc_type, count in cases_by_type.items() if count)
    expected_present = [doc_type for doc_type in EXPECTED_DOC_TYPES if cases_by_type.get(doc_type, 0)]
    coverage_denominator = len(EXPECTED_DOC_TYPES)
    return {
        "dataset_id": summary.get("dataset_id"),
        "cases": summary.get("cases", len(results)),
        "cases_by_type": cases_by_type,
        "doc_types_present": present,
        "expected_doc_types_present": expected_present,
        "doc_type_coverage": round(len(expected_present) / coverage_denominator, 3),
    }


def extraction_layer(summary: dict) -> dict:
    field_accuracy = summary.get("field_accuracy") or {}
    return {
        "headline_billed_total_accuracy": summary.get("headline_billed_total_accuracy"),
        "field_accuracy": field_accuracy,
        "min_field_accuracy": min(field_accuracy.values(), default=None),
        "accuracy_by_doc_type": summary.get("accuracy_by_doc_type") or {},
    }


def matching_layer(summary: dict, results: list[dict]) -> dict:
    by_method: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in results:
        method = row.get("method") or "unknown"
        by_method[method][0] += int(bool(row.get("correct")))
        by_method[method][1] += 1
    return {
        "match_accuracy": summary.get("match_accuracy"),
        "input_mode": summary.get("input_mode"),
        "method_accuracy": {
            method: round(hit / total, 3) if total else None
            for method, (hit, total) in sorted(by_method.items())
        },
        "method_counts": {
            method: total
            for method, (_hit, total) in sorted(by_method.items())
        },
    }


def speed_layer(summary: dict, results: list[dict]) -> dict:
    elapsed = [row.get("elapsed_sec") for row in results if isinstance(row.get("elapsed_sec"), (int, float))]
    return {
        "avg_elapsed_sec": summary.get("avg_elapsed_sec"),
        "max_elapsed_sec": summary.get("max_elapsed_sec") if summary.get("max_elapsed_sec") is not None
        else (max(elapsed) if elapsed else None),
    }


def failure_layer(kind: str, summary: dict, results: list[dict]) -> dict:
    if kind == "matching":
        incorrect = [row.get("file") for row in results if not row.get("correct")]
        categories: Counter = Counter()
        for row in results:
            if row.get("correct"):
                continue
            if row.get("predicted_row") is None:
                categories["null_prediction"] += 1
            elif row.get("raw") and "{" not in str(row.get("raw")):
                categories["llm_parse_fail"] += 1
            else:
                categories["wrong_row"] += 1
        return {
            "parse_failures": summary.get("parse_failures", 0),
            "incorrect_cases": len(incorrect),
            "incorrect_files": incorrect,
            "failure_categories": dict(sorted(categories.items())),
        }

    field_failures: Counter = Counter()
    categories: Counter = Counter()
    case_failures = 0
    parse_failures = int(summary.get("parse_failures", 0) or 0)
    if parse_failures:
        categories["parse_failure"] += parse_failures
    for row in results:
        match = row.get("match") or {}
        parse_failure_row = row.get("status") not in {None, "ok"}
        failed = [] if parse_failure_row else [field for field, ok in match.items() if not ok]
        if failed:
            case_failures += 1
            categories["field_mismatch"] += 1
            field_failures.update(failed)
        if row.get("verify_retried"):
            categories["arithmetic_retry"] += 1
        raw = str(row.get("raw") or "")
        if "@@@" in raw:
            categories["degenerate_raw"] += 1
    return {
        "parse_failures": parse_failures,
        "incorrect_cases": case_failures + parse_failures,
        "field_failures": dict(sorted(field_failures.items())),
        "failure_categories": dict(sorted(categories.items())),
    }


def provenance_layer(summary: dict) -> dict:
    run_config = summary.get("run_config") or {}
    return {
        "model": summary.get("model"),
        "tag": summary.get("tag"),
        "manifest_sha256": summary.get("manifest_sha256"),
        "ground_truth_sha256": run_config.get("ground_truth_sha256"),
        "ledger_truth_sha256": run_config.get("ledger_truth_sha256"),
        "prompt_sha256": run_config.get("prompt_sha256") or run_config.get("prompt_template_sha256"),
    }


def quality_gate_layer(kind: str, layers: dict, *, target: float = DEFAULT_ACCURACY_TARGET) -> dict:
    failures = layers["failures"]
    reasons: list[str] = []
    manual_review_triggers: list[str] = []

    if failures.get("parse_failures", 0):
        reasons.append("parse_failure")
        manual_review_triggers.append("parse_failure")
    if failures.get("incorrect_cases", 0):
        reasons.append("incorrect_cases")
        manual_review_triggers.append("incorrect_cases")

    if kind == "matching":
        accuracy = layers["matching"].get("match_accuracy")
        if not valid_number(accuracy, maximum=1) or accuracy < target:
            reasons.append("match_accuracy_below_target")
            manual_review_triggers.append("match_accuracy_below_target")
    else:
        min_field_accuracy = layers["extraction"].get("min_field_accuracy")
        if not valid_number(min_field_accuracy, maximum=1) or min_field_accuracy < target:
            reasons.append("field_accuracy_below_target")
            manual_review_triggers.append("field_accuracy_below_target")

    failure_categories = failures.get("failure_categories") or {}
    if failure_categories.get("wrong_row"):
        manual_review_triggers.append("amount_or_row_mismatch")
    if failure_categories.get("null_prediction"):
        manual_review_triggers.append("candidate_not_selected")
    if failure_categories.get("llm_parse_fail"):
        manual_review_triggers.append("llm_parse_failure")

    return {
        "target_accuracy": target,
        "status": "pass" if not reasons else "human_review_required",
        "reasons": sorted(set(reasons)),
        "manual_review_triggers": sorted(set(manual_review_triggers)),
        "auto_retry_limit": 1,
        "guarantee": "best_effort_not_perfect_guarantee",
    }


def build_layers(result: dict) -> dict:
    validate_result(result)
    summary = result.get("summary") or {}
    results = result.get("results") or []
    kind = result_kind(result)
    layers = {
        "kind": kind,
        "dataset": dataset_layer(summary, results),
        "speed": speed_layer(summary, results),
        "failures": failure_layer(kind, summary, results),
        "provenance": provenance_layer(summary),
    }
    if kind == "matching":
        layers["matching"] = matching_layer(summary, results)
    else:
        layers["extraction"] = extraction_layer(summary)
    layers["quality_gate"] = quality_gate_layer(kind, layers)
    return layers


def render_markdown(path: Path, layers: dict) -> str:
    lines = [
        f"# Metric layers: {path.name}",
        "",
        f"- kind: `{layers['kind']}`",
        f"- cases: `{layers['dataset']['cases']}`",
        f"- doc_type_coverage: `{layers['dataset']['doc_type_coverage']}`",
        f"- avg_elapsed_sec: `{layers['speed']['avg_elapsed_sec']}`",
        f"- max_elapsed_sec: `{layers['speed']['max_elapsed_sec']}`",
        f"- incorrect_cases: `{layers['failures']['incorrect_cases']}`",
        f"- quality_gate: `{layers['quality_gate']['status']}`",
    ]
    if layers["kind"] == "matching":
        lines.append(f"- match_accuracy: `{layers['matching']['match_accuracy']}`")
    else:
        lines.append(
            "- headline_billed_total_accuracy: "
            f"`{layers['extraction']['headline_billed_total_accuracy']}`"
        )
        lines.append(f"- min_field_accuracy: `{layers['extraction']['min_field_accuracy']}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="result JSON を metric layer に分解する")
    parser.add_argument("result_json")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args()

    path = Path(args.result_json)
    try:
        layers = build_layers(load_result(path))
    except (ValueError, OSError) as e:
        print(f"metric layer 生成を停止しました: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    if args.json:
        print(json.dumps(layers, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render_markdown(path, layers))


if __name__ == "__main__":
    main()
