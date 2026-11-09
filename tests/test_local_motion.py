"""Local recovery must bind the rig to the reviewed source and never start Modal."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_pipeline.batch import Job, digest
from asset_pipeline.local_motion import motion_sources, validate_rig
from asset_pipeline.providers import Providers


class LocalMotionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.rig = self.root / "rigged.glb"
        self.rig.write_bytes(b"glTFfixture")
        self.model = self.root / "model.glb"
        self.model.write_bytes(b"glTFreviewed")
        self.rig.with_suffix(".provenance.json").write_text(
            json.dumps({"input": {"sha256": digest(self.model)}})
        )

    def test_rig_is_bound_to_exact_reviewed_model(self):
        self.assertEqual(validate_rig(self.rig, self.model)["input"]["sha256"], digest(self.model))
        self.model.write_bytes(b"glTFchanged")
        with self.assertRaisesRegex(ValueError, "reviewed model"):
            validate_rig(self.rig, self.model)

    def test_only_explicit_local_motion_skips_cloud_budget(self):
        local = Providers(Path("blender"), local_rig=self.rig, motion_repo=self.root)
        for stage in ("motion-test", "animate"):
            self.assertFalse(local.requires_cloud(stage, None))
            self.assertTrue(Providers(Path("blender")).requires_cloud(stage, None))
        for stage in ("reference", "model", "refine"):
            self.assertTrue(local.requires_cloud(stage, None))

    def test_local_failure_never_falls_back_to_paid_cloud(self):
        local = Providers(Path("blender"), local_rig=self.rig, motion_repo=self.root)
        job = Job("fixture", self.model, {"moves": ["idle"]})
        self.model.write_bytes(b"glTFchanged")
        with patch.object(local, "cloud") as cloud:
            with self.assertRaisesRegex(ValueError, "reviewed model"):
                local._motion(
                    job, {"finish": {"files": {"model.glb": str(self.model)}}}, self.root, True
                )
            cloud.assert_not_called()

    def test_local_recovery_requires_both_rig_and_motion_source(self):
        with self.assertRaisesRegex(ValueError, "together"):
            Providers(Path("blender"), local_rig=self.rig)

    def test_motion_review_tracks_preset_bytes_and_metadata(self):
        for name in (
            "scripts/kimodo_retarget.py",
            "scripts/arm_clearance.py",
            "image_to_3dlab/motions/presets.json",
            "image_to_3dlab/motions/idle.npz",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original")
        original = motion_sources(self.root)
        (self.root / "image_to_3dlab/motions/idle.npz").write_bytes(b"changed")
        self.assertNotEqual(original, motion_sources(self.root))
