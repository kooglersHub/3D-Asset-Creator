import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from hi3d_support import geometry_manifest, resolve_coarse, validate_settings
from quality_workflow import sha256
from review_asset import export_review


class Hi3DIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / "front.png"
        self.reference.write_bytes(b"reference")
        (self.root / "raw_model.glb").write_bytes(b"glTFraw")
        (self.root / "model.glb").write_bytes(b"glTFfinished")
        self.manifest = {"moves": [], "reference_hashes": {"front": sha256(self.reference)}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_refines_raw_mesh_instead_of_decimated_finish(self):
        source = resolve_coarse("", str(self.root), self.reference)
        self.assertEqual(self.root / "raw_model.glb", source)

    def test_refuses_a_different_reference_image(self):
        self.reference.write_bytes(b"different pose")
        with self.assertRaisesRegex(ValueError, "same reference"):
            resolve_coarse("", str(self.root), self.reference)

    def test_refuses_animated_coarse_export(self):
        self.manifest["moves"] = ["idle"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "static export"):
            resolve_coarse("", str(self.root), self.reference)

    def test_requires_exactly_one_coarse_source(self):
        with self.assertRaises(ValueError):
            resolve_coarse(str(self.root / "model.glb"), str(self.root), self.reference)

    def test_refuses_invalid_resolution_before_cloud_call(self):
        with self.assertRaises(ValueError):
            validate_settings(768, 25)

    def test_geometry_cannot_receive_character_approval(self):
        self.manifest = geometry_manifest("image", "mesh", 512, 25, 0, True)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "geometry-only"):
            export_review(self.root, "model")
        self.assertFalse(self.manifest["textures_preserved"])
        self.assertIn("normalized", self.manifest["coordinate_space"])
        self.assertEqual("Powered by Hi3D 1.0", self.manifest["attribution"])


if __name__ == "__main__":
    unittest.main()
