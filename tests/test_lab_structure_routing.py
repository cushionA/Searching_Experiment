import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jse.lab import __main__ as lab_main
from jse.lab.state import LabError


class StructureRoutingCliTests(unittest.TestCase):
    def args(self, run, *options):
        return lab_main.argparse.Namespace(run=Path(run), action="execute", ids="a,b", phase="warm",
                                           live=True, output=None)

    @patch("jse.lab.__main__.subprocess.run")
    def test_dispatches_isolated_node_command_with_options(self, node_run):
        node_run.return_value = subprocess.CompletedProcess([], 0, '{"status":"ok"}', "")
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "routing-run"
            args = self.args(run)
            result = lab_main.run_structure_routing(args)
            command = node_run.call_args.args[0]
            self.assertEqual(result, {"status": "ok"})
            self.assertEqual(command[0], "node")
            self.assertEqual(command[1], str(Path(lab_main.__file__).resolve().parents[2] / "experiments" / "structure-routing" / "sitemap-agent-routing.mjs"))
            self.assertEqual(command[command.index("--run") + 1], str(run.resolve()))
            self.assertEqual(command[command.index("--action") + 1], "execute")
            self.assertEqual(command[command.index("--ids") + 1], "a,b")
            self.assertEqual(command[command.index("--phase") + 1], "warm")
            self.assertIn("--live", command)
            self.assertEqual(node_run.call_args.kwargs["cwd"], Path(lab_main.__file__).resolve().parents[2])
            self.assertIs(node_run.call_args.kwargs["shell"], False)

    @patch("jse.lab.__main__.subprocess.run")
    def test_export_output_is_forwarded(self, node_run):
        node_run.return_value = subprocess.CompletedProcess([], 0, '{"exported":true}', "")
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "routing-run"
            output = Path(temp) / "out.zip"
            args = self.args(run)
            args.action, args.ids, args.live, args.output = "export", None, False, output
            self.assertEqual(lab_main.run_structure_routing(args), {"exported": True})
            command = node_run.call_args.args[0]
            self.assertEqual(command[command.index("--output") + 1], str(output.resolve()))
            self.assertNotIn("--live", command)

    @patch("jse.lab.__main__.subprocess.run")
    def test_grounding_state_is_refused_before_node_dispatch(self, node_run):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "state.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(LabError):
                lab_main.run_structure_routing(self.args(run))
        node_run.assert_not_called()

    @patch("jse.lab.__main__.subprocess.run")
    def test_non_json_and_node_errors_are_reported(self, node_run):
        with tempfile.TemporaryDirectory() as temp:
            node_run.return_value = subprocess.CompletedProcess([], 0, "html", "")
            with self.assertRaisesRegex(LabError, "JSON"):
                lab_main.run_structure_routing(self.args(Path(temp) / "run"))
            node_run.return_value = subprocess.CompletedProcess([], 2, "", "x" * 3000)
            with self.assertRaises(LabError) as raised:
                lab_main.run_structure_routing(self.args(Path(temp) / "run"))
            self.assertLessEqual(len(str(raised.exception)), 2040)

    def test_export_requires_output(self):
        self.assertEqual(lab_main.main(["structure-routing", "--run", "r", "--action", "export"]), 2)


if __name__ == "__main__":
    unittest.main()
