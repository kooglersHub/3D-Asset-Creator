"""Create two real clips and run the production Godot package adapter without AI/cloud."""

import math
import json
import struct
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from asset_pipeline.batch import Job  # noqa: E402
from asset_pipeline.providers import Providers  # noqa: E402


def smoke():
    output = ROOT / "exports/godot-smoke"
    source = output / "source"
    source.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.object.armature_add()
    rig = bpy.context.object
    rig.name = "Skeleton"
    rig.data.bones[0].name = "RightHand"
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0.5))
    mesh = bpy.context.object
    mesh.parent = rig
    modifier = mesh.modifiers.new("Skin", "ARMATURE")
    modifier.object = rig
    mesh.vertex_groups.new(name="RightHand").add(list(range(8)), 1.0, "REPLACE")
    bpy.ops.export_scene.gltf(filepath=str(source / "rigged.glb"), export_format="GLB")
    for name, axis in [("idle", "z"), ("sword_swing", "x")]:
        # Independent source files must not inherit another clip's Blender actions.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(source / "rigged.glb"))
        rig = next(obj for obj in bpy.data.objects if obj.type == "ARMATURE")
        pose = rig.pose.bones["RightHand"]
        pose.rotation_mode = "XYZ"
        pose.rotation_euler = (0, 0, 0)
        pose.keyframe_insert("rotation_euler", frame=1)
        setattr(pose.rotation_euler, axis, math.pi / 2)
        pose.keyframe_insert("rotation_euler", frame=10)
        rig.animation_data.action.name = name
        bpy.ops.export_scene.gltf(
            filepath=str(source / f"{name}.glb"),
            export_format="GLB",
            export_animation_mode="ACTIVE_ACTIONS",
        )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=0.1)
    bpy.context.object.scale.z = 4
    bpy.ops.export_scene.gltf(filepath=str(source / "weapon.glb"), export_format="GLB")
    job = Job(
        "Fixture",
        source / "weapon.glb",
        {
            "kind": "humanoid",
            "moves": ["idle", "sword_swing"],
            "weapon": {"hand_bone": "RightHand", "position": [0, 0, 0.5]},
        },
    )
    files = {
        f"export/{name}.glb": str(source / f"{name}.glb")
        for name in ["rigged", "idle", "sword_swing"]
    }
    Providers(Path(bpy.app.binary_path)).package(
        job,
        {
            "animate": {"files": files},
            "finish": {"files": {"weapon/model.glb": str(source / "weapon.glb")}},
        },
        output,
    )
    data = (output / "Assets/Generated/Fixture/model.glb").read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + length])
    names = {animation["name"] for animation in document["animations"]}
    assert names == {"idle", "sword_swing"}, names
    (output / "project.godot").write_text(
        '[application]\nconfig/name="Generated asset smoke"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
    )
    import shutil

    shutil.copy2(ROOT / "tests/godot_package_smoke.gd", output / "test.gd")
    print("Two clips packaged with matching skeleton and separate rigid weapon:", output)


smoke()
