"""Synthetic boundary tests of the real aggregation and command-line entry point."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import metric_layers as metrics

ROOT = Path(__file__).resolve().parent


def matching():
    return {"summary": {"cases": 1, "match_accuracy": 1}, "results": [{"file": "fictional", "correct": True}]}


def extraction():
    return {"summary": {"cases": 1, "field_accuracy": {"vendor": 1}}, "results": [{"match": {"vendor": True}}]}


class InputValidationTests(unittest.TestCase):
    def test_nonfinite_matching_accuracy_cannot_pass(self):
        for value in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(value=value):
                result = matching()
                result["summary"]["match_accuracy"] = value
                with self.assertRaisesRegex(ValueError, "match_accuracy"):
                    metrics.build_layers(result)

    def test_out_of_range_and_boolean_matching_accuracy_cannot_pass(self):
        for value in [2, -0.1, True, False, "1", 10 ** 400]:
            with self.subTest(value=value):
                result = matching()
                result["summary"]["match_accuracy"] = value
                with self.assertRaisesRegex(ValueError, "match_accuracy"):
                    metrics.build_layers(result)

    def test_invalid_extraction_accuracy_cannot_pass(self):
        for value in [float("nan"), float("inf"), 2, -0.1, True, "1", None]:
            with self.subTest(value=value):
                result = extraction()
                result["summary"]["field_accuracy"]["vendor"] = value
                with self.assertRaisesRegex(ValueError, "field_accuracy.vendor"):
                    metrics.build_layers(result)

    def test_string_false_and_numeric_correct_flags_are_not_counted_as_hits(self):
        for value in ["false", "true", 0, 1, None, [], {}]:
            with self.subTest(value=value):
                result = matching()
                result["results"][0]["correct"] = value
                with self.assertRaisesRegex(ValueError, "1件目.*correct"):
                    metrics.build_layers(result)

    def test_string_false_extraction_flag_is_not_counted_as_a_match(self):
        result = extraction()
        result["results"][0]["match"]["vendor"] = "false"
        with self.assertRaisesRegex(ValueError, "1件目.*match.vendor"):
            metrics.build_layers(result)

    def test_actual_false_retains_review_status_and_failure_counts(self):
        result = matching()
        result["results"][0]["correct"] = False
        layers = metrics.build_layers(result)
        self.assertEqual(layers["quality_gate"]["status"], "human_review_required")
        self.assertEqual(layers["matching"]["method_accuracy"]["unknown"], 0)
        self.assertEqual(layers["failures"]["incorrect_cases"], 1)
        result = extraction()
        result["results"][0]["match"]["vendor"] = False
        self.assertEqual(metrics.build_layers(result)["failures"]["field_failures"], {"vendor": 1})

    def test_good_numeric_boundaries_and_default_target_remain_supported(self):
        for value, expected in [(0, "human_review_required"), (0.9989, "human_review_required"), (0.999, "pass"), (1, "pass")]:
            with self.subTest(value=value):
                result = matching()
                result["summary"]["match_accuracy"] = value
                self.assertEqual(metrics.build_layers(result)["quality_gate"]["status"], expected)

    def test_missing_accuracy_remains_review_required(self):
        result = matching()
        result["summary"]["match_accuracy"] = None
        self.assertEqual(metrics.build_layers(result)["quality_gate"]["status"], "human_review_required")
        result = extraction()
        result["summary"]["field_accuracy"] = {}
        self.assertEqual(metrics.build_layers(result)["quality_gate"]["status"], "human_review_required")

    def test_invalid_count_types_do_not_produce_reported_metrics(self):
        for key in ["cases", "parse_failures"]:
            for value in [-1, 1.5, True, "1"]:
                with self.subTest(key=key, value=value):
                    result = matching()
                    result["summary"][key] = value
                    with self.assertRaisesRegex(ValueError, key):
                        metrics.build_layers(result)

    def test_invalid_nested_containers_fail_with_field_reason(self):
        for key in ["cases_by_type", "field_accuracy", "accuracy_by_doc_type", "run_config"]:
            with self.subTest(key=key):
                result = extraction()
                result["summary"][key] = [1]
                with self.assertRaisesRegex(ValueError, key):
                    metrics.build_layers(result)

    def test_invalid_case_type_count_is_rejected(self):
        result = matching()
        result["summary"]["cases_by_type"] = {"invoice": -1}
        with self.assertRaisesRegex(ValueError, "cases_by_type"):
            metrics.build_layers(result)

    def test_nonobject_root_and_record_have_clear_errors(self):
        for result in [None, [], "result", 1]:
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, "オブジェクト"):
                metrics.build_layers(result)
        result = matching()
        result["results"].append(None)
        with self.assertRaisesRegex(ValueError, "2件目"):
            metrics.build_layers(result)

    def test_invalid_record_identity_and_match_shape_are_rejected(self):
        for key, value in [("doc_type", []), ("method", {}), ("status", []), ("match", [])]:
            with self.subTest(key=key):
                result = extraction()
                result["results"][0][key] = value
                with self.assertRaisesRegex(ValueError, key):
                    metrics.build_layers(result)

    def test_nonfinite_negative_or_boolean_times_are_rejected(self):
        for value in [float("inf"), float("nan"), -1, True, "1"]:
            for key in ["avg_elapsed_sec", "max_elapsed_sec"]:
                with self.subTest(key=key, value=value):
                    result = matching()
                    result["summary"][key] = value
                    with self.assertRaisesRegex(ValueError, key):
                        metrics.build_layers(result)
            result = matching()
            result["results"][0]["elapsed_sec"] = value
            with self.assertRaisesRegex(ValueError, "elapsed_sec"):
                metrics.build_layers(result)

    def test_original_synthetic_example_and_summary_only_input_are_preserved(self):
        result = json.loads((ROOT / "examples/synthetic-result.json").read_text("utf-8"))
        before = copy.deepcopy(result)
        layers = metrics.build_layers(result)
        self.assertEqual(layers["dataset"]["cases"], 2)
        self.assertEqual(layers["extraction"]["min_field_accuracy"], 0.5)
        self.assertEqual(layers["failures"]["incorrect_cases"], 1)
        self.assertEqual(layers["quality_gate"]["status"], "human_review_required")
        self.assertEqual(result, before)
        self.assertEqual(metrics.build_layers({"summary": {"cases": 2, "field_accuracy": {"vendor": 1}}})["quality_gate"]["status"], "pass")

    def test_direct_quality_gate_refuses_invalid_accuracy(self):
        layers = metrics.build_layers(matching())
        for value in [float("nan"), float("inf"), True, 2]:
            layers["matching"]["match_accuracy"] = value
            self.assertEqual(metrics.quality_gate_layer("matching", layers)["status"], "human_review_required")


class CliTests(unittest.TestCase):
    def run_cli(self, content, json_output=True):
        with tempfile.TemporaryDirectory(prefix="synthetic-metrics-") as temp:
            path = Path(temp) / "fixture.json"
            path.write_text(content, encoding="utf-8")
            args = [sys.executable, "-B", "-X", "utf8", str(ROOT / "metric_layers.py"), str(path)]
            return subprocess.run(args + (["--json"] if json_output else []), capture_output=True, text=True, encoding="utf-8")

    def assert_duplicate_rejected(self, content, key):
        for json_output in [True, False]:
            with self.subTest(json_output=json_output):
                result = self.run_cli(content, json_output=json_output)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("重複", result.stderr)
                self.assertIn(key, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_duplicate_accuracy_does_not_choose_a_passing_value(self):
        for first in [0, 1]:
            with self.subTest(first=first):
                self.assert_duplicate_rejected('{"summary":{"cases":1,"match_accuracy":' + str(first) + ',"match_accuracy":1},"results":[{"correct":true}]}', "match_accuracy")

    def test_cli_duplicate_correct_does_not_turn_failure_into_hit(self):
        for first in ["false", "true"]:
            with self.subTest(first=first):
                self.assert_duplicate_rejected('{"summary":{"cases":1,"match_accuracy":1},"results":[{"correct":' + first + ',"correct":true}]}', "correct")

    def test_cli_duplicate_top_level_container_is_rejected(self):
        for content, key in [
            ('{"summary":{"cases":1,"match_accuracy":0},"summary":{"cases":1,"match_accuracy":1}}', "summary"),
            ('{"summary":{"cases":1,"match_accuracy":1},"results":[{"correct":false}],"results":[{"correct":true}]}', "results")]:
            with self.subTest(key=key):
                self.assert_duplicate_rejected(content, key)

    def test_cli_duplicate_nested_fields_and_metadata_are_rejected(self):
        for container, fields, key in [
            ("field_accuracy", '"vendor":0,"vendor":1', "vendor"),
            ("cases_by_type", '"invoice":1,"invoice":2', "invoice"),
            ("run_config", '"prompt_sha256":"first","prompt_sha256":"second"', "prompt_sha256")]:
            with self.subTest(container=container):
                self.assert_duplicate_rejected('{"summary":{"cases":1,"match_accuracy":1,"' + container + '":{' + fields + '}}}', key)

    def test_cli_unicode_escape_alias_is_detected_after_json_decoding(self):
        self.assert_duplicate_rejected(r'{"summary":{"cases":1,"match_accuracy":0,"match\u005faccuracy":1}}', "match_accuracy")

    def test_cli_same_fields_in_distinct_cases_remain_valid(self):
        content = json.dumps({"summary":{"cases":2,"match_accuracy":0.5},"results":[{"correct":True,"method":"synthetic"},{"correct":False,"method":"synthetic"}]})
        result = self.run_cli(content)
        self.assertEqual(result.returncode, 0)
        layers = json.loads(result.stdout)
        self.assertEqual(layers["matching"]["method_accuracy"], {"synthetic":0.5})
        self.assertEqual(layers["failures"]["incorrect_cases"], 1)
        self.assertEqual(layers["quality_gate"]["status"], "human_review_required")

    def test_cli_nan_and_infinity_never_emit_pass_or_stdout(self):
        for value in ["NaN", "Infinity", "-Infinity", "1e309"]:
            with self.subTest(value=value):
                result = self.run_cli('{"summary":{"cases":1,"match_accuracy":' + value + '},"results":[{"correct":true}]}')
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("生成を停止", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_second_record_error_emits_no_partial_output(self):
        result = matching()
        result["results"].append({"correct": "false"})
        output = self.run_cli(json.dumps(result))
        self.assertEqual(output.returncode, 1)
        self.assertEqual(output.stdout, "")
        self.assertIn("2件目", output.stderr)

    def test_cli_good_input_has_strict_json_and_markdown_outputs(self):
        result = self.run_cli(json.dumps(matching()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["quality_gate"]["status"], "pass")
        result = self.run_cli(json.dumps(extraction()), json_output=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("quality_gate: `pass`", result.stdout)

    def test_cli_human_review_retains_exit_zero(self):
        result = matching()
        result["results"][0]["correct"] = False
        output = self.run_cli(json.dumps(result))
        self.assertEqual(output.returncode, 0)
        self.assertEqual(json.loads(output.stdout)["quality_gate"]["status"], "human_review_required")

    def test_cli_missing_file_has_clear_error_without_traceback(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-metrics-") as temp:
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "metric_layers.py"), str(Path(temp) / "missing.json")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("生成を停止", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class EmptyDatasetTests(unittest.TestCase):
    def assert_no_cases(self, result):
        layers = metrics.build_layers(result)
        self.assertEqual(layers["dataset"]["cases"], 0)
        self.assertEqual(layers["quality_gate"]["status"], "human_review_required")
        self.assertIn("no_cases", layers["quality_gate"]["reasons"])
        self.assertIn("no_cases", layers["quality_gate"]["manual_review_triggers"])
        return layers

    def test_zero_matching_cases_cannot_pass(self):
        self.assert_no_cases({"summary": {"cases": 0, "match_accuracy": 1}, "results": []})

    def test_zero_extraction_cases_cannot_pass(self):
        self.assert_no_cases({"summary": {"cases": 0, "field_accuracy": {"vendor": 1}}, "results": []})

    def test_omitted_count_and_empty_results_require_review(self):
        for accuracy in [{"match_accuracy": 1}, {"field_accuracy": {"vendor": 1}}]:
            with self.subTest(accuracy=accuracy):
                self.assert_no_cases({"summary": accuracy, "results": []})

    def test_summary_only_zero_count_requires_review(self):
        for accuracy in [{"match_accuracy": 1}, {"field_accuracy": {"vendor": 1}}]:
            with self.subTest(accuracy=accuracy):
                self.assert_no_cases({"summary": {"cases": 0, **accuracy}})

    def test_cli_empty_dataset_emits_review_in_both_modes_with_exit_zero(self):
        content = json.dumps({"summary": {"cases": 0, "match_accuracy": 1}, "results": []})
        runner = CliTests()
        for json_output in [True, False]:
            with self.subTest(json_output=json_output):
                output = runner.run_cli(content, json_output=json_output)
                self.assertEqual(output.returncode, 0)
                self.assertEqual(output.stderr, "")
                if json_output:
                    self.assertEqual(json.loads(output.stdout)["quality_gate"]["reasons"], ["no_cases"])
                else:
                    self.assertIn("quality_gate: `human_review_required`", output.stdout)

    def test_direct_gate_missing_or_invalid_case_count_never_passes(self):
        layers = metrics.build_layers(matching())
        for value in [None, 0, -1, True, False, "1", 1.0, float("nan")]:
            with self.subTest(value=value):
                layers["dataset"]["cases"] = value
                gate = metrics.quality_gate_layer("matching", layers)
                self.assertEqual(gate["status"], "human_review_required")
                self.assertIn("no_cases", gate["reasons"])
        del layers["dataset"]
        self.assertIn("no_cases", metrics.quality_gate_layer("matching", layers)["reasons"])

    def test_zero_cases_retains_other_review_reasons(self):
        layers = self.assert_no_cases({"summary": {"cases": 0, "match_accuracy": 0.5, "parse_failures": 1}})
        self.assertEqual(layers["quality_gate"]["reasons"], ["match_accuracy_below_target", "no_cases", "parse_failure"])
        self.assertEqual(layers["failures"]["parse_failures"], 1)

    def test_summary_only_positive_cases_remain_supported(self):
        for accuracy in [{"match_accuracy": 1}, {"field_accuracy": {"vendor": 1}}]:
            with self.subTest(accuracy=accuracy):
                result = {"summary": {"cases": 1, **accuracy}}
                before = copy.deepcopy(result)
                self.assertEqual(metrics.build_layers(result)["quality_gate"]["status"], "pass")
                self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
