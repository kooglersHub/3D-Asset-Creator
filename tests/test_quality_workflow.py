import sys
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import assetfurnace_modal_worker as worker
from quality_workflow import (
    check_review,
    extract_export,
    parse_moves,
    review_template,
    sha256,
    validate_options,
)


def options(stage="model", **changes):
    return {
        "stage": stage,
        "detail": "more",
        "moves": "idle",
        "generate_back": False,
        "compare_details": [],
        "arms_away": 8,
        "texture_size": 2048,
        "reviews": {},
        **changes,
    }


def accepted(kind, hashes, asset_id=None):
    review = review_template(kind, hashes, asset_id)
    review["accepted"] = True
    review["checks"] = dict.fromkeys(review["checks"], True)
    return review


class WorkflowValidationTests(unittest.TestCase):
    def test_archive_cannot_write_outside_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.glb", b"glTF")
            with self.assertRaisesRegex(ValueError, "escapes"):
                extract_export(archive, root / "export")
            self.assertFalse((root / "outside.glb").exists())

    def test_changed_geometry_invalidates_review(self):
        review = accepted("model", {"model": "old"}, "asset")
        with self.assertRaisesRegex(ValueError, "stale"):
            check_review(review, "model", {"model": "new"}, "asset")

    def test_partial_review_cannot_unlock_generation(self):
        review = accepted("model", {"model": "same"}, "asset")
        review["checks"]["no_holes_or_fused_equipment"] = False
        with self.assertRaisesRegex(ValueError, "Complete"):
            check_review(review, "model", {"model": "same"}, "asset")

    def test_motion_test_rejects_a_batch(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_options(options("motion-test", moves="idle,walk"))

    def test_new_image_cannot_skip_model_review(self):
        with self.assertRaisesRegex(ValueError, "static model"):
            validate_options(options("animate"), has_image=True)

    def test_move_cannot_escape_export_directory(self):
        with self.assertRaises(ValueError):
            parse_moves("idle,../../outside")

    def test_back_view_requires_front_and_excludes_generation(self):
        with self.assertRaises(ValueError):
            validate_options(options(), has_back=True)
        with self.assertRaises(ValueError):
            validate_options(options(generate_back=True), has_image=True, has_back=True)

    def test_review_template_does_not_auto_accept(self):
        review = review_template("model", {"model": "hash"}, "asset")
        self.assertFalse(review["accepted"])
        self.assertFalse(any(review["checks"].values()))


class PipelineStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.folder = self.root / "export"
        self.folder.mkdir()
        for name in ("raw", "finished", "game", "rigged", "idle", "walk"):
            (self.output / f"{name}.glb").write_bytes(b"glTF" + name.encode())
        self.record = {
            "id": "selected",
            "model": "raw.glb",
            "finished": "finished.glb",
            "rigged": "rigged.glb",
            "clips": [],
        }
        self.api = SimpleNamespace(
            create=Mock(return_value={"run_id": "created"}),
            continue_asset=Mock(return_value={"run_id": "continued"}),
            list_moves=Mock(
                return_value={"installed": True, "moves": [{"id": "idle"}, {"id": "walk"}]}
            ),
            run_status=Mock(return_value={"status": "done", "asset_id": "selected"}),
        )
        self.studio = Mock()
        self.studio.get.side_effect = self.records
        self.patcher = patch.object(worker, "ASSETFURNACE", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.args = SimpleNamespace(
            asset_id="selected", input=None, back_input=None, character="Knight", backend="pixal3d"
        )

    def records(self, _path):
        return {"assets": [self.record, {"id": "variant", "finished": "game.glb"}]}

    def uploaded_rig(self):
        self.record.pop("model")
        self.record.pop("finished")
        source = self.output / "input/source.glb"
        source.parent.mkdir()
        source.write_bytes((self.output / "raw.glb").read_bytes())
        (self.output / "rigged.provenance.json").write_text(
            json.dumps({"input": {"sha256": sha256(source)}})
        )
        return source

    def test_uploaded_rig_resumes_from_provenance_without_repeating_rigging(self):
        source = self.uploaded_rig()
        self.args.input = self.output / "raw.glb"
        self.args.asset_id = None
        reviews = {"model": accepted("model", {"model": sha256(source)})}
        self.record["clips"] = [{"name": "idle", "file": "idle.glb"}]
        meta = worker.run_pipeline(
            self.studio,
            self.api,
            self.args,
            options("motion-test", reviews=reviews, reuse_rig=True),
            self.folder,
        )
        self.assertEqual(meta["asset_id"], "selected")
        self.assertEqual(sha256(self.folder / "model.glb"), sha256(source))
        self.api.create.assert_not_called()
        self.api.continue_asset.assert_not_called()

    def test_changed_uploaded_rig_source_stops_before_any_new_generation(self):
        source = self.uploaded_rig()
        self.args.input = self.output / "raw.glb"
        self.args.asset_id = None
        reviews = {"model": accepted("model", {"model": sha256(source)})}
        source.write_bytes(b"glTFchanged")
        with self.assertRaisesRegex(ValueError, "provenance"):
            worker.run_pipeline(
                self.studio,
                self.api,
                self.args,
                options("motion-test", reviews=reviews, reuse_rig=True),
                self.folder,
            )
        self.api.create.assert_not_called()
        self.api.continue_asset.assert_not_called()

    def test_static_model_never_rigs_or_lists_moves(self):
        self.args.input = self.root / "front.png"
        meta = worker.run_pipeline(self.studio, self.api, self.args, options(), self.folder)
        self.api.list_moves.assert_not_called()
        self.api.continue_asset.assert_not_called()
        self.assertEqual("finished", self.api.create.call_args.kwargs["stop_after_step"])
        self.assertEqual([], meta["moves"])
        self.assertTrue((self.folder / "raw_model.glb").is_file())
        self.assertFalse((self.folder / "rigged.glb").exists())

    def test_back_view_and_detail_reach_create(self):
        self.args.input = self.root / "front.png"
        self.args.back_input = self.root / "back.png"
        worker.run_pipeline(
            self.studio, self.api, self.args, options(texture_size=4096), self.folder
        )
        kwargs = self.api.create.call_args.kwargs
        self.assertEqual(str(self.args.back_input), kwargs["back_image_path"])
        self.assertEqual(4096, kwargs["advanced"]["texture_size"])
        self.assertEqual("more", kwargs["finish_detail"])

    def test_comparison_keeps_selected_variant_and_source(self):
        self.args.input = self.root / "front.png"
        self.api.run_status.side_effect = [
            {"status": "done", "asset_id": "selected"},
            {"status": "done", "asset_id": "variant"},
        ]
        meta = worker.run_pipeline(
            self.studio, self.api, self.args, options(compare_details=["game", "more"]), self.folder
        )
        self.assertEqual("selected", meta["asset_id"])
        self.assertEqual("selected", self.api.continue_asset.call_args.kwargs["asset_id"])
        self.assertTrue(self.api.continue_asset.call_args.kwargs["again"])
        self.assertEqual("variant", meta["variants"]["game"]["asset_id"])

    def test_stale_model_stops_before_rigging(self):
        reviews = {"model": accepted("model", {"model": "stale"}, "selected")}
        with self.assertRaisesRegex(ValueError, "stale"):
            worker.run_pipeline(
                self.studio,
                self.api,
                self.args,
                options("motion-test", reviews=reviews),
                self.folder,
            )
        self.api.continue_asset.assert_not_called()
        self.api.list_moves.assert_not_called()

    def test_reviewed_external_glb_skips_finish_and_keeps_pbr_surface(self):
        self.args.asset_id = None
        self.args.input = self.output / "raw.glb"
        self.record.pop("finished")
        reviews = {"model": accepted("model", {"model": sha256(self.args.input)})}

        def continue_clip(*_args, **kwargs):
            self.record["clips"].append({"name": kwargs["move"], "file": "idle.glb"})
            return {"run_id": "continued"}

        self.api.continue_asset.side_effect = continue_clip
        worker.run_pipeline(
            self.studio, self.api, self.args, options("motion-test", reviews=reviews), self.folder
        )
        created = self.api.create.call_args.kwargs
        self.assertFalse(created["finish_upload"])
        self.assertEqual(created["stop_after_step"], "rigged")
        self.assertEqual(created["advanced"]["rig_from"], "model")
        self.assertTrue(self.api.continue_asset.call_args.kwargs["again"])
        self.assertEqual(sha256(self.args.input), sha256(self.folder / "model.glb"))

    def test_stale_external_surface_is_rejected_before_cloud_creation(self):
        self.args.asset_id = None
        self.args.input = self.output / "raw.glb"
        with self.assertRaisesRegex(ValueError, "stale"):
            worker.run_pipeline(
                self.studio,
                self.api,
                self.args,
                options("motion-test", reviews={"model": accepted("model", {"model": "old"})}),
                self.folder,
            )
        self.api.create.assert_not_called()

    def test_motion_review_required_before_expanding(self):
        reviews = {
            "model": accepted("model", {"model": sha256(self.output / "finished.glb")}, "selected")
        }
        with self.assertRaisesRegex(ValueError, "test clip"):
            worker.run_pipeline(
                self.studio, self.api, self.args, options("animate", reviews=reviews), self.folder
            )
        self.api.continue_asset.assert_not_called()

    def test_test_clip_uses_armor_settings_and_preserves_model(self):
        hashes = {"model": sha256(self.output / "finished.glb")}
        reviews = {"model": accepted("model", hashes, "selected")}

        def continue_clip(*_args, **kwargs):
            self.record["clips"].append({"name": kwargs["move"], "file": kwargs["move"] + ".glb"})
            return {"run_id": "continued"}

        self.api.continue_asset.side_effect = continue_clip
        meta = worker.run_pipeline(
            self.studio, self.api, self.args, options("motion-test", reviews=reviews), self.folder
        )
        self.assertEqual(["idle"], meta["moves"])
        self.assertEqual(8, self.api.continue_asset.call_args.kwargs["advanced"]["arms_away"])
        self.assertEqual(hashes["model"], sha256(self.folder / "model.glb"))

    def test_reviewed_clip_change_blocks_expansion(self):
        hashes = {"model": sha256(self.output / "finished.glb")}
        self.record["clips"] = [{"name": "idle", "file": "idle.glb"}]
        reviews = {
            "model": accepted("model", hashes, "selected"),
            "motion": accepted("motion", {**hashes, "idle": "old"}, "selected"),
        }
        with self.assertRaisesRegex(ValueError, "stale"):
            worker.run_pipeline(
                self.studio, self.api, self.args, options("animate", reviews=reviews), self.folder
            )
        self.api.continue_asset.assert_not_called()

    def test_expansion_reuses_test_and_applies_clearance_to_every_move(self):
        hashes = {"model": sha256(self.output / "finished.glb")}
        self.record["clips"] = [{"name": "idle", "file": "idle.glb"}]
        reviews = {
            "model": accepted("model", hashes, "selected"),
            "motion": accepted(
                "motion", {**hashes, "idle": sha256(self.output / "idle.glb")}, "selected"
            ),
        }

        def continue_clip(*_args, **kwargs):
            self.record["clips"].append({"name": kwargs["move"], "file": kwargs["move"] + ".glb"})
            return {"run_id": "continued"}

        self.api.continue_asset.side_effect = continue_clip
        meta = worker.run_pipeline(
            self.studio,
            self.api,
            self.args,
            options("animate", moves="idle,walk", arms_away=12, reviews=reviews),
            self.folder,
        )
        self.assertEqual(["idle", "walk"], meta["moves"])
        self.api.continue_asset.assert_called_once()
        self.assertEqual(12, self.api.continue_asset.call_args.kwargs["advanced"]["arms_away"])


if __name__ == "__main__":
    unittest.main()
