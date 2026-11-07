"""Bind explicitly selected equipment to a bone, preserving a source GLB."""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector


def rigid_weights(obj, indices, bone, armature):
    indices = list(indices)
    if not indices or min(indices) < 0 or max(indices) >= len(obj.data.vertices):
        raise ValueError("Part selection contains invalid vertex indices")
    for group in obj.vertex_groups:
        group.remove(indices)
    group = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
    group.add(indices, 1, "REPLACE")
    modifier = next((mod for mod in obj.modifiers if mod.type == "ARMATURE"), None)
    if modifier is None:
        modifier = obj.modifiers.new("Rigid attachment", "ARMATURE")
    modifier.object = armature
    # glTF expects skinned meshes under the armature. Bake any prop-placement
    # parent into its transform first, keeping the rest-space placement unchanged.
    bpy.context.view_layer.update()
    transform = obj.matrix_world.copy()
    obj.parent = armature
    obj.matrix_world = transform


def bind_parts(source, config):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise ValueError("Rigid binding requires exactly one armature")
    armature = armatures[0]
    for part in config["parts"]:
        bone = part["bone"]
        if bone not in armature.data.bones:
            raise ValueError(f"Unknown bone: {bone}")
        if "source" in part:
            before = set(bpy.context.scene.objects)
            prop_path = Path(part["source"]).resolve()
            bpy.ops.import_scene.gltf(filepath=str(prop_path))
            imported = set(bpy.context.scene.objects) - before
            if any(obj.type == "ARMATURE" for obj in imported):
                raise ValueError("Import an unrigged prop")
            objects = [obj for obj in imported if obj.type == "MESH"]
            if not objects:
                raise ValueError("Prop contains no mesh")
            # Offsets describe the prop placement in the character's rest space.
            root = bpy.data.objects.new("Equipment placement", None)
            bpy.context.scene.collection.objects.link(root)
            for obj in imported:
                if obj.parent not in imported:
                    transform = obj.matrix_world.copy()
                    obj.parent = root
                    obj.matrix_world = transform
            root.location = Vector(part.get("offset", (0, 0, 0)))
            root.rotation_euler = Euler(part.get("rotation", (0, 0, 0)))
            root.scale *= part.get("scale", 1)
        else:
            obj = bpy.data.objects.get(part["object"])
            if obj is None or obj.type != "MESH":
                raise ValueError(f"Unknown mesh: {part['object']}")
            objects = [obj]
        for obj in objects:
            indices = part.get("vertices", list(range(len(obj.data.vertices))))
            rigid_weights(obj, indices, bone, armature)
    return armature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--parts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Choose a new output file to preserve the original")
    config = json.loads(args.parts.read_text(encoding="utf-8"))
    for part in config["parts"]:
        if "source" in part:
            part["source"] = str((args.parts.resolve().parent / part["source"]).resolve())
    bind_parts(args.input.resolve(), config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.output.resolve()),
        export_format="GLB",
        export_animations=True,
        export_skins=True,
    )
    print("Repaired export needs a new model and motion review before use.")


if __name__ == "__main__":
    main()
