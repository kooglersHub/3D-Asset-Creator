"""Concrete stage adapters. Cloud apps are loaded lazily and reused per batch stage."""

from __future__ import annotations

import contextlib
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .batch import atomic_json, digest
from .models import MODELS

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


class Providers:
    def __init__(self, blender: Path):
        self.blender = blender
        self.stack = None
        self.apps = {}
        self.log_directory = None

    @contextlib.contextmanager
    def session(self, stage):
        self.apps = {}
        with contextlib.ExitStack() as self.stack:
            yield self
        self.stack = None

    def cloud(self, name):
        if name not in self.apps:
            sys.path.insert(0, str(TOOLS))
            module = importlib.import_module(f"tools.{name}")
            self.stack.enter_context(module.app.run())
            self.apps[name] = module
        return self.apps[name]

    def blender_run(self, script, *arguments):
        if not self.blender.is_file():
            raise FileNotFoundError("Set --blender to the Blender 4.2+ executable")
        command = [
            str(self.blender),
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(TOOLS / script),
            "--",
            *map(str, arguments),
        ]
        if self.log_directory is None:
            subprocess.run(command, check=True)
        else:
            log = self.log_directory / "blender.log"
            print(f"  Blender log: {log}", flush=True)
            with log.open("a", encoding="utf-8") as output:
                subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT)

    def execute(self, stage, job, inputs, folder):
        print(f"[{job.identifier}] {stage}", flush=True)
        self.log_directory = folder
        metadata = getattr(self, stage.replace("-", "_"))(job, inputs, folder) or {}
        files = {
            str(path.relative_to(folder)).replace("\\", "/"): str(path.resolve())
            for path in folder.rglob("*")
            if path.is_file()
        }
        return {"files": files, "metadata": metadata}

    def reference(self, job, inputs, folder):
        module = self.cloud("modal_references")
        weapon = job.config.get("weapon", {}).get("image")
        pictures = module.generate.remote(
            job.source.read_bytes(), job.config, Path(weapon).read_bytes() if weapon else None
        )
        for name, data in pictures.items():
            if name not in {"front", "back", "weapon"}:
                raise ValueError("Unexpected reference image name")
            (folder / f"{name}.png").write_bytes(data)
        atomic_json(
            folder / "provenance.json",
            {"models": MODELS, "source": digest(job.source), "config": job.config},
        )

    def model(self, job, inputs, folder):
        module = self.cloud("modal_trellis2")
        # TRELLIS.2 is single-view; the back view is a review reference, not conditioning.
        for role in ("front", "weapon"):
            reference = inputs["reference"]["files"].get(f"{role}.png")
            if reference:
                data = module.generate_png_to_glb.remote(
                    Path(reference).read_bytes(),
                    job.config["resolution"],
                    max(job.config["triangles"] * 4, 10000),
                    job.config["texture_size"],
                    job.config["seed"],
                )
                (folder / ("coarse.glb" if role == "front" else "weapon.glb")).write_bytes(data)
        atomic_json(folder / "provenance.json", MODELS["trellis2"])

    def refine(self, job, inputs, folder):
        coarse = Path(inputs["model"]["files"]["coarse.glb"])
        if not job.config["refine"]:
            shutil.copy2(coarse, folder / "model.glb")
            return {"skipped": True, "reason": "Hi3D is optional; no measured quality win yet"}
        module = self.cloud("modal_hi3d")
        from quality_workflow import extract_export

        reference = Path(inputs["reference"]["files"]["front.png"])
        remote = module.refine_geometry.remote(
            reference.read_bytes(), coarse.read_bytes(), 512, 25, job.config["seed"], True
        )
        archive = folder / "hi3d.zip"
        with archive.open("wb") as output:
            for chunk in module.volume.read_file(remote):
                output.write(chunk)
        extract_export(archive, folder / "hi3d")
        shutil.copy2(folder / "hi3d/model.glb", folder / "model.glb")
        return {"geometry_only": True, "resolution": 512}

    def finish(self, job, inputs, folder):
        arguments = [
            "--coarse",
            inputs["model"]["files"]["coarse.glb"],
            "--output",
            folder / "model.glb",
            "--triangles",
            job.config["triangles"],
            "--texture-size",
            job.config["texture_size"],
            "--height",
            job.config["height"],
        ]
        if job.config["refine"]:
            arguments.extend(["--high", inputs["refine"]["files"]["model.glb"]])
        self.blender_run("finish_character.py", *arguments)
        weapon = inputs["model"]["files"].get("weapon.glb")
        if weapon:
            self.blender_run(
                "finish_character.py",
                "--coarse",
                weapon,
                "--output",
                folder / "weapon/model.glb",
                "--triangles",
                5000,
                "--texture-size",
                job.config["texture_size"],
                "--height",
                job.config["weapon"].get("length", 1.2),
            )
        atomic_json(folder / "manifest.json", {"stage": "model", "moves": [], "variants": {}})
        self.blender_run("preview_asset.py", "--export", folder)

    def motion_test(self, job, inputs, folder):
        return self._motion(job, inputs, folder, test=True)

    def animate(self, job, inputs, folder):
        return self._motion(job, inputs, folder, test=False)

    def _motion(self, job, inputs, folder, test):
        module = self.cloud("modal_assetfurnace")
        from quality_workflow import MODEL_CHECKS, MOTION_CHECKS, extract_export

        model = Path(inputs["finish"]["files"]["model.glb"])
        model_review = {
            "kind": "model",
            "accepted": True,
            "hashes": {"model": digest(model)},
            "checks": dict.fromkeys(MODEL_CHECKS, True),
        }
        moves = [job.config.get("test_move", "idle")] if test else job.config["moves"]
        options = {
            "stage": "motion-test" if test else "animate",
            "detail": "more",
            "arms_away": 8,
            "texture_size": job.config["texture_size"],
            "moves": ",".join(moves),
            "compare_details": [],
            "reviews": {"model": model_review},
        }
        identifier = ""
        if not test:
            manifest = json.loads(
                Path(inputs["motion-test"]["files"]["export/manifest.json"]).read_text()
            )
            identifier = manifest["asset_id"]
            model_review["asset_id"] = identifier
            tested = manifest["moves"][0]
            clip = Path(inputs["motion-test"]["files"][f"export/{tested}.glb"])
            options["reviews"]["motion"] = {
                "kind": "motion",
                "accepted": True,
                "asset_id": identifier,
                "hashes": {"model": digest(model), tested: digest(clip)},
                "checks": dict.fromkeys(MOTION_CHECKS, True),
            }
        archive_name, data = module.generate_character.remote(
            job.identifier,
            model.name if test else "",
            model.read_bytes() if test else None,
            identifier,
            ",".join(moves),
            options,
        )
        archive = folder / "export.zip"
        with archive.open("wb") as output:
            if data is not None:
                output.write(data)
            else:
                for chunk in module.volume.read_file(f"exports/{archive_name}"):
                    output.write(chunk)
        extract_export(archive, folder / "export")
        self.blender_run("check_blender_imports.py", folder / "export")
        self.blender_run("preview_asset.py", "--export", folder / "export", "--clips")
        return json.loads((folder / "export/manifest.json").read_text())

    def package(self, job, inputs, folder):
        destination = folder / "Assets/Generated" / job.identifier
        destination.mkdir(parents=True, exist_ok=True)
        prefix = f"res://Assets/Generated/{job.identifier}"
        if job.config["kind"] == "prop":
            shutil.copy2(inputs["finish"]["files"]["model.glb"], destination / "model.glb")
        else:
            files = inputs["animate"]["files"]
            spec = {
                "rig": files["export/rigged.glb"],
                "clips": {move: files[f"export/{move}.glb"] for move in job.config["moves"]},
                "output": str(destination / "model.glb"),
                "hand_bone": job.config.get("weapon", {}).get("hand_bone", "auto")
                if job.config.get("weapon")
                else "",
            }
            atomic_json(folder / "assembly.json", spec)
            self.blender_run("assemble_character.py", "--spec", folder / "assembly.json")
            skeleton = json.loads((destination / "model.json").read_text())
            shutil.copy2(
                ROOT / "godot/generated_character.gd", destination / "generated_character.gd"
            )
            lines = [
                "[gd_scene load_steps=3 format=3]",
                f'[ext_resource type="Script" path="{prefix}/generated_character.gd" id="1"]',
                f'[ext_resource type="PackedScene" path="{prefix}/model.glb" id="2"]',
            ]
            weapon = inputs["finish"]["files"].get("weapon/model.glb")
            if weapon:
                lines[0] = "[gd_scene load_steps=4 format=3]"
                shutil.copy2(weapon, destination / "weapon.glb")
                lines.append(f'[ext_resource type="PackedScene" path="{prefix}/weapon.glb" id="3"]')
            lines.extend(
                [
                    '[node name="GeneratedCharacter" type="Node3D"]',
                    'script = ExtResource("1")',
                    'model_scene = ExtResource("2")',
                ]
            )
            if weapon:
                lines.extend(
                    [
                        'weapon_scene = ExtResource("3")',
                        f"hand_bone = &{json.dumps(skeleton['hand_bone'])}",
                    ]
                )
                config = job.config["weapon"]
                for property_name, config_name in (
                    ("weapon_position", "position"),
                    ("weapon_rotation_degrees", "rotation_degrees"),
                ):
                    values = config.get(config_name, [0, 0, 0])
                    lines.append(f"{property_name} = Vector3({', '.join(map(str, values))})")
                lines.append(f"weapon_scale = {config.get('scale', 1.0)}")
            (destination / "character.tscn").write_text("\n".join(lines) + "\n")
        atomic_json(
            destination / "provenance.json",
            {
                "source_sha256": digest(job.source),
                "models": MODELS,
                "job": job.config,
                "runtime_playtest": "pending",
            },
        )
        (folder / "IMPORT.md").write_text(
            "Copy Assets into the Godot project root. Instance character.tscn (humanoid) "
            "or model.glb (prop). Keep the existing combat controller and call set_speed(), "
            "attack(), hit(), die() on the visual adapter. Check facing, sword grip, root "
            "motion and attack timing in the actual running game. No game files were modified.\n"
        )
        return {"godot_asset": str(destination), "runtime_playtest": "pending"}
