"""Tests for scripts/benchmark_aggregator.py."""

import contextlib
import inspect
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import scripts.benchmark_aggregator as bm_mod
from scripts.benchmark_aggregator import parse_args, run_benchmark

_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


class TrackingTemporaryDirectory:
    """Wrapper around tempfile.TemporaryDirectory to capture created directory paths."""

    instances = []

    def __init__(self, *args, **kwargs):
        self._dir = _ORIGINAL_TEMPORARY_DIRECTORY(*args, **kwargs)
        self.name = self._dir.name
        TrackingTemporaryDirectory.instances.append(self.name)

    def __enter__(self):
        return self._dir.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._dir.__exit__(exc_type, exc_val, exc_tb)

    def cleanup(self):
        return self._dir.cleanup()


class TestBenchmarkAggregator(unittest.TestCase):
    """Test suite verifying safety, determinism, performance, and cleanup of benchmark_aggregator.

    All tests execute inside an isolated TemporaryDirectory sandbox as the working directory,
    ensuring zero read/write/delete interactions with the actual repository workspace or sample_data.
    """

    def setUp(self):
        self.orig_cwd = os.getcwd()
        self.sandbox = _ORIGINAL_TEMPORARY_DIRECTORY(prefix="test_sandbox_")
        os.chdir(self.sandbox.name)
        TrackingTemporaryDirectory.instances = []

    def tearDown(self):
        os.chdir(self.orig_cwd)
        self.sandbox.cleanup()

    def test_preexisting_sentinel_remains_unchanged(self):
        """Verify that a preexisting sample_data/benchmark_ledger.csv in cwd is never overwritten or deleted."""
        os.makedirs("sample_data", exist_ok=True)
        sentinel_path = os.path.join("sample_data", "benchmark_ledger.csv")
        sentinel_content = (
            "월,디비전,거래선,모델,매출,비용1,비용2,비용3,영업이익\n"
            "202601,USER_SENTINEL_DIV,USER_CUST,USER_MOD,9999,111,222,333,444\n"
        )
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write(sentinel_content)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = run_benchmark(n_rows=50)

        # Sentinel file must still exist and content must be identical
        self.assertTrue(
            os.path.exists(sentinel_path),
            "Preexisting sample_data/benchmark_ledger.csv was unexpectedly deleted!",
        )
        with open(sentinel_path, "r", encoding="utf-8") as f:
            current_content = f.read()
        self.assertEqual(
            current_content,
            sentinel_content,
            "Preexisting sample_data/benchmark_ledger.csv was overwritten or modified!",
        )
        self.assertEqual(results["n_rows"], 50)

    def test_no_fixed_benchmark_files_created_in_sandbox(self):
        """Verify that running the benchmark creates no fixed benchmark files in sample_data or cwd."""
        sentinel_path = os.path.join("sample_data", "benchmark_ledger.csv")
        self.assertFalse(os.path.exists(sentinel_path))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_benchmark(n_rows=50)

        # Neither sample_data/benchmark_ledger.csv nor output files must be created in sandbox cwd
        self.assertFalse(
            os.path.exists(sentinel_path),
            "benchmark_ledger.csv was improperly created in sample_data directory.",
        )
        self.assertFalse(
            os.path.exists(os.path.join("sample_data", "benchmark_summary.xlsx")),
            "benchmark_summary.xlsx was improperly created in sample_data directory.",
        )
        self.assertFalse(
            os.path.exists("benchmark_ledger.csv"),
            "benchmark_ledger.csv was improperly created in root cwd.",
        )
        self.assertFalse(
            os.path.exists("benchmark_summary.xlsx"),
            "benchmark_summary.xlsx was improperly created in root cwd.",
        )

    def test_temporary_files_cleanup_on_aggregation_failure(self):
        """Verify that temporary files and directory are cleaned up even when aggregation fails."""
        with patch("scripts.benchmark_aggregator.tempfile.TemporaryDirectory", TrackingTemporaryDirectory):
            with patch("scripts.benchmark_aggregator.aggregate_dataset", side_effect=RuntimeError("Aggregation failure")):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(RuntimeError) as ctx:
                        run_benchmark(n_rows=50)
                self.assertIn("Aggregation failure", str(ctx.exception))

        self.assertEqual(
            len(TrackingTemporaryDirectory.instances),
            1,
            "Expected exactly 1 TemporaryDirectory to be created.",
        )
        tmp_dir_path = TrackingTemporaryDirectory.instances[0]
        self.assertFalse(
            os.path.exists(tmp_dir_path),
            f"Temporary directory {tmp_dir_path} was not cleaned up after aggregation failure!",
        )

    def test_temporary_files_cleanup_on_schema_inspection_failure(self):
        """Verify that temporary files and directory are cleaned up even when schema inspection fails."""
        with patch("scripts.benchmark_aggregator.tempfile.TemporaryDirectory", TrackingTemporaryDirectory):
            with patch("scripts.benchmark_aggregator.inspect_dataset_schema", side_effect=ValueError("Schema error")):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(ValueError) as ctx:
                        run_benchmark(n_rows=50)
                self.assertIn("Schema error", str(ctx.exception))

        self.assertEqual(len(TrackingTemporaryDirectory.instances), 1)
        tmp_dir_path = TrackingTemporaryDirectory.instances[0]
        self.assertFalse(
            os.path.exists(tmp_dir_path),
            f"Temporary directory {tmp_dir_path} was not cleaned up after schema failure!",
        )

    def test_preexisting_sentinel_remains_unchanged_on_failure(self):
        """Verify sentinel file in sandbox is not touched even when aggregation fails."""
        os.makedirs("sample_data", exist_ok=True)
        sentinel_path = os.path.join("sample_data", "benchmark_ledger.csv")
        sentinel_content = "SENTINEL_FAIL_TEST,999\n"
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write(sentinel_content)

        with patch("scripts.benchmark_aggregator.aggregate_dataset", side_effect=RuntimeError("Fail mid-run")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(RuntimeError):
                    run_benchmark(n_rows=50)

        self.assertTrue(os.path.exists(sentinel_path))
        with open(sentinel_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), sentinel_content)

    def test_deterministic_data_generation_with_local_rng(self):
        """Verify that local NumPy random generator is used and global random state is not mutated."""
        # Capture whatever global random state currently exists without mutating it
        state_before = np.random.get_state()

        with patch("numpy.random.seed") as mock_seed:
            buf1 = io.StringIO()
            with contextlib.redirect_stdout(buf1):
                res1 = run_benchmark(n_rows=50, seed=42)
            mock_seed.assert_not_called()

        state_after = np.random.get_state()
        self.assertEqual(state_before[0], state_after[0])
        np.testing.assert_array_equal(state_before[1], state_after[1])
        self.assertEqual(state_before[2], state_after[2])
        self.assertEqual(state_before[3], state_after[3])
        self.assertEqual(state_before[4], state_after[4])

        # Verify determinism: same seed produces identical results
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            res2 = run_benchmark(n_rows=50, seed=42)

        self.assertEqual(res1["result_rows"], res2["result_rows"])
        self.assertEqual(res1["schema"].columns, res2["schema"].columns)

    def test_default_row_count_preserves_500000(self):
        """Verify that the default row count parameter and CLI default remain 500,000."""
        sig = inspect.signature(run_benchmark)
        self.assertEqual(
            sig.parameters["n_rows"].default,
            500_000,
            "run_benchmark default n_rows must be 500,000",
        )
        parsed = parse_args([])
        self.assertEqual(
            parsed.rows,
            500_000,
            "CLI default rows must be 500,000",
        )

    def test_fast_execution_and_metadata_cleanup_labeling(self):
        """Verify benchmark completes for small row counts and returns clear cleanup metadata."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = run_benchmark(n_rows=50)

        self.assertEqual(res["n_rows"], 50)
        self.assertGreater(res["result_rows"], 0)
        self.assertTrue(res["cleaned_up"])
        self.assertIn("cleaned_up_output_path", res)
        self.assertIn("cleaned_up_temp_dir", res)
        # Ensure temporary outputs do NOT exist on disk
        self.assertFalse(os.path.exists(res["cleaned_up_temp_dir"]))
        self.assertFalse(os.path.exists(res["cleaned_up_output_path"]))
        # Timing fields are present and valid numeric values
        self.assertIsInstance(res["gen_elapsed"], (int, float))
        self.assertGreaterEqual(res["gen_elapsed"], 0.0)
        self.assertIsInstance(res["inspect_elapsed"], (int, float))
        self.assertGreaterEqual(res["inspect_elapsed"], 0.0)
        self.assertIsInstance(res["agg_elapsed"], (int, float))
        self.assertGreaterEqual(res["agg_elapsed"], 0.0)

    def test_output_contains_schema_and_aggregate_timings(self):
        """Verify that benchmark output prints useful schema inspection and aggregation timings."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_benchmark(n_rows=50)
        output = buf.getvalue()

        self.assertIn("Generating synthetic dataset with 50 rows", output)
        self.assertIn("Schema inspection took:", output)
        self.assertIn("Detected month:", output)
        self.assertIn("Dimensions:", output)
        self.assertIn("Measures:", output)
        self.assertIn("Aggregation completed in:", output)
        self.assertIn("Output saved to:", output)
        self.assertIn("Cleaned up benchmark files.", output)

    def test_cli_argument_parsing(self):
        """Verify CLI argument parsing for rows, seed, and chunksize."""
        parsed = parse_args(["--rows", "123", "--seed", "77", "--chunksize", "50"])
        self.assertEqual(parsed.rows, 123)
        self.assertEqual(parsed.seed, 77)
        self.assertEqual(parsed.chunksize, 50)

    def test_module_docstring_documents_repo_root_invocation(self):
        """Verify that module docstring clearly documents invocation from repository root."""
        doc = bm_mod.__doc__
        self.assertIsNotNone(doc)
        self.assertIn("python scripts/benchmark_aggregator.py", doc)
        self.assertIn("repository root", doc.lower())


if __name__ == "__main__":
    unittest.main()
