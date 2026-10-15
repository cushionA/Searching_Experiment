"""Synthetic safety-boundary tests for classical runner assembly and coverage."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    import run_classical_sku_v1 as runner
except ModuleNotFoundError as error:
    if error.name in {"jaconv", "pint"}:
        runner = None
    else:
        raise


def write_rows(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


@unittest.skipIf(runner is None, "install jaconv and Pint in the dedicated venv")
class ClassicalRunTests(unittest.TestCase):
    def source_fixture(self, root: Path, *, extra_task=False, missing_au_row=False):
        source = root / "inputs"
        source.mkdir()
        request = {"request_id": "req-1", "direction": "rakuten_to_au",
                   "axis_name": "Size", "selected_value": "100cm",
                   "option_values": ["100cm", "200cm"], "target_document_id": "au-1",
                   "target_selected_axes": [{"axis_name": "Size", "value": "1m"}]}
        requests = [request]
        tasks = [{"task_id": "task-1", "case_id": "case-1", "au_row_key": "row-1",
                  "request_id": "req-1"}]
        bindings = [{"task_id": "task-1", "request_id": "req-1"}]
        if extra_task:
            requests.append({**request, "request_id": "req-2", "selected_value": "200cm"})
            tasks.append({"task_id": "task-2", "case_id": "case-1", "au_row_key": "row-1",
                          "request_id": "req-2"})
        rows = [{"case_id": "case-1", "au_row_key": "row-1", "bindings": bindings}]
        cases = [{"case_id": "case-1", "row_keys": ["row-1", "row-2"]
                  if missing_au_row else ["row-1"]}]
        documents = [{"document_id": "au-1", "dossier_id": "case-1", "side": "au"},
                     {"document_id": "rak-1", "dossier_id": "case-1", "side": "rakuten"}]
        for name, values in (("requests.jsonl", requests), ("documents.jsonl", documents),
                             ("tasks.jsonl", tasks), ("row-plans.jsonl", rows),
                             ("case-plans.jsonl", cases)):
            write_rows(source / name, values)
        return source

    def test_unknown_extra_au_row_prevents_case_acceptance(self):
        rows = [
            {"case_id": "case-1", "au_row_key": "row-match", "bindings": [{"request_id": "r1"}]},
            {"case_id": "case-1", "au_row_key": "row-extra", "bindings": [{"request_id": "r2"}]},
        ]
        row_outputs, decisions = runner.assemble(rows, {"r1": "support", "r2": "unknown"})
        self.assertEqual([row["state"] for row in row_outputs], ["support", "unknown"])
        self.assertEqual(decisions, [{"case_id": "case-1", "decision": "drop",
                                     "disposition": "review", "au_row_key": None}])

    def test_unbound_original_condition_fails_before_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source_fixture(root, extra_task=True)
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "all original conditions must be bound exactly once"):
                runner.run(source, output)
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])

    def test_omitted_au_row_fails_before_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source_fixture(root, missing_au_row=True)
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "all original AU rows must be retained"):
                runner.run(source, output)
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
