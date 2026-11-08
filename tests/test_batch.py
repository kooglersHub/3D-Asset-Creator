"""Offline batch contract tests: resumes, independent failures, reviews and cost limits."""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from asset_pipeline.batch import Job, RunLock, Runner, Store, load_jobs


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.fail = set()

    @contextlib.contextmanager
    def session(self, stage):
        yield self

    def execute(self, stage, job, inputs, folder):
        self.calls.append((job.identifier, stage))
        if (job.identifier, stage) in self.fail:
            raise RuntimeError("intentional fixture failure")
        output = folder / "artifact.txt"
        output.write_text(job.identifier + stage)
        return {"files": {"artifact": str(output)}}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = self.root / "source.png"
        source.write_bytes(b"image fixture")
        self.jobs = [
            Job(name, source, {"kind": "humanoid", "refine": False}) for name in ["one", "two"]
        ]
        self.store = Store(self.root / "output")
        self.provider = FakeProvider()
        self.runner = Runner(self.store, self.provider)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def run_cloud(self, **kwargs):
        return self.runner.run(self.jobs, execute_cloud=True, max_cloud_tasks=20, **kwargs)

    def test_no_cloud_without_explicit_execution(self):
        self.runner.run(self.jobs)
        self.assertEqual(self.provider.calls, [])

    def test_task_cap_and_review_stop(self):
        self.runner.run(self.jobs, execute_cloud=True, max_cloud_tasks=1)
        self.assertEqual(self.provider.calls, [("one", "reference")])
        self.run_cloud()
        self.assertEqual(self.provider.calls, [("one", "reference"), ("two", "reference")])

    def test_stage_major_resume_and_complete_flow(self):
        self.run_cloud()
        for job in self.jobs:
            self.store.approve(job, "reference", "visually inspected")
        self.run_cloud()
        self.assertEqual(
            self.provider.calls[2:6],
            [(name, stage) for stage in ["model", "refine"] for name in ["one", "two"]],
        )
        self.assertEqual(len(self.provider.calls), 8)
        for gate in ["finish", "motion-test", "animate"]:
            for job in self.jobs:
                self.store.approve(job, gate, "reviewed")
            self.run_cloud()
        self.assertEqual(len(self.provider.calls), 14)
        self.run_cloud()
        self.assertEqual(len(self.provider.calls), 14)

    def test_failure_does_not_block_other_job_or_auto_retry(self):
        self.provider.fail.add(("one", "reference"))
        self.run_cloud()
        self.assertEqual(self.store.task(self.jobs[0], "reference")["status"], "failed")
        self.assertEqual(self.store.task(self.jobs[1], "reference")["status"], "complete")
        self.run_cloud()
        self.assertEqual(len(self.provider.calls), 2)
        self.provider.fail.clear()
        self.run_cloud(retry_failed=True)
        self.assertEqual(len(self.provider.calls), 3)

    def test_changed_artifact_invalidates_review(self):
        self.run_cloud()
        job = self.jobs[0]
        self.store.approve(job, "reference", "reviewed")
        row = self.store.task(job, "reference")
        result = json.loads(row["result"])
        Path(result["files"]["artifact"]).write_text("modified")
        self.assertFalse(self.store.reviewed(job, "reference"))
        with self.assertRaises(ValueError):
            self.store.approve(job, "reference", "reviewed")
        summary = self.run_cloud()
        self.assertIn(("one", "reference", "corrupt-output"), summary)
        self.assertNotIn(("one", "model"), self.provider.calls)

    def test_review_bound_to_actual_output_even_after_retry(self):
        self.run_cloud()
        job = self.jobs[0]
        self.store.approve(job, "reference", "reviewed")
        result = json.loads(self.store.task(job, "reference")["result"])
        result["checksums"] = {"different-output": "different-hash"}
        self.store.record(job, "reference", "complete", result)
        self.assertFalse(self.store.reviewed(job, "reference"))

    def test_source_changes_create_new_unreviewed_job_version(self):
        self.run_cloud()
        self.store.approve(self.jobs[0], "reference", "reviewed")
        self.jobs[0].source.write_bytes(b"new image")
        self.assertIsNone(self.store.task(self.jobs[0], "reference"))
        self.assertFalse(self.store.reviewed(self.jobs[0], "reference"))

    def test_only_one_runner_can_lock_batch(self):
        with RunLock(self.root / "lock"):
            with self.assertRaises(RuntimeError):
                with RunLock(self.root / "lock"):
                    pass

    def test_props_finish_and_package_without_humanoid_rigging(self):
        self.jobs = [Job("prop", self.jobs[0].source, {"kind": "prop", "refine": False})]
        self.run_cloud()
        self.store.approve(self.jobs[0], "reference", "mask inspected")
        self.run_cloud()
        self.store.approve(self.jobs[0], "finish", "mesh inspected")
        self.runner.run(self.jobs)
        self.assertEqual(
            [stage for _, stage in self.provider.calls],
            ["reference", "model", "refine", "finish", "package"],
        )

    def test_retry_preserves_failed_attempt_files(self):
        self.run_cloud()
        original = json.loads(self.store.task(self.jobs[0], "reference")["result"])
        self.store.record(self.jobs[0], "reference", "failed", error="fixture interrupted")
        self.run_cloud(retry_failed=True)
        replacement = json.loads(self.store.task(self.jobs[0], "reference")["result"])
        self.assertNotEqual(original["files"], replacement["files"])
        self.assertTrue(Path(original["files"]["artifact"]).exists())

    def test_manifest_rejects_windows_collisions_and_bad_resolution(self):
        manifest = self.root / "batch.json"
        for jobs in [
            [{"id": "CON", "image": "source.png"}],
            [{"id": "Knight", "image": "source.png"}, {"id": "knight", "image": "source.png"}],
            [{"id": "knight", "image": "source.png", "resolution": 99}],
        ]:
            manifest.write_text(json.dumps({"version": 1, "jobs": jobs}))
            with self.assertRaises(ValueError):
                load_jobs(manifest)


if __name__ == "__main__":
    unittest.main()
