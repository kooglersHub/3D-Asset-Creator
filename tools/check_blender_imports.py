"""Verify that Blender can import each export with a deformable skeleton."""

import json
import sys
from pathlib import Path

import bpy


def load_character(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if bpy.ops.import_scene.gltf(filepath=str(path)) != {"FINISHED"}:
        raise ValueError(f"Blender could not import {path.name}")
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    meshes = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
        and obj.vertex_groups
        and any(mod.type == "ARMATURE" and mod.object in armatures for mod in obj.modifiers)
    ]
    if not armatures or not meshes:
        raise ValueError(f"{path.name} has no skinned mesh linked to an armature")
    return armatures, meshes


def capture_pose(armatures, frame):
    bpy.context.scene.frame_set(frame)
    return [bone.matrix.copy() for obj in armatures for bone in obj.pose.bones]


def changes_pose(armatures, action):
    first, last = map(int, action.frame_range)
    reference = capture_pose(armatures, first)
    for frame in range(first + 1, last + 1, max(1, (last - first) // 8)):
        sample = capture_pose(armatures, frame)
        if any(
            abs(a[r][c] - b[r][c]) > 1e-4
            for a, b in zip(reference, sample)
            for r in range(4)
            for c in range(4)
        ):
            return True
    return False


def inspect_export(path, animated):
    armatures, meshes = load_character(path)
    actions = [
        obj.animation_data.action
        for obj in armatures
        if obj.animation_data and obj.animation_data.action
    ]
    if animated and not any(changes_pose(armatures, action) for action in actions):
        raise ValueError(f"{path.name} has no action that changes the skeleton pose")
    return {
        "file": path.name,
        "bones": sum(len(obj.data.bones) for obj in armatures),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "actions": [action.name for action in actions],
    }


def main():
    directory = Path(sys.argv[sys.argv.index("--") + 1])
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("stage") in {"model", "prop", "geometry-review"}:
        files = {"model.glb"}
        files.update(variant["file"] for variant in manifest.get("variants", {}).values())
        if (directory / "raw_model.glb").is_file():
            files.add("raw_model.glb")
        rows = []
        for filename in sorted(files):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.import_scene.gltf(filepath=str(directory / filename))
            meshes = [
                obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.visible_get()
            ]
            if not meshes or not any(obj.data.polygons for obj in meshes):
                raise ValueError(f"{filename} contains no visible surface")
            rows.append(
                {
                    "file": filename,
                    "triangles": sum(
                        len(poly.vertices) - 2 for obj in meshes for poly in obj.data.polygons
                    ),
                    "visual_quality": "requires_human_review",
                }
            )
        print(json.dumps(rows, indent=2))
        return
    rows = [inspect_export(directory / "rigged.glb", animated=False)]
    rows.extend(
        inspect_export(directory / f"{move}.glb", animated=True) for move in manifest["moves"]
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
