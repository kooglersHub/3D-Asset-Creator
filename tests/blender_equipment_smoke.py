"""Run with Blender --background --python-exit-code 1 --python this_file."""

import math
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from bind_rigid_parts import bind_parts, rigid_weights


def evaluated_points(obj):
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    return [evaluated.matrix_world @ vertex.co for vertex in evaluated.data.vertices]


def smoke():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.object.armature_add()
    armature = bpy.context.object
    armature.name = "Character"
    bone = armature.data.bones[0]
    bone.name = "hand"
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.5, 0, 0.5))
    prop = bpy.context.object
    prop.name = "Sword"
    # A deliberately bad initial weight becomes rigid during the repair.
    prop.vertex_groups.new(name="unrelated").add(list(range(8)), 0.5, "REPLACE")
    pose = armature.pose.bones["hand"]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.z = 0
    pose.keyframe_insert("rotation_euler", frame=1)
    pose.rotation_euler.z = math.pi / 2
    pose.keyframe_insert("rotation_euler", frame=10)
    bpy.context.scene.frame_set(1)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "character.glb"
        bpy.ops.export_scene.gltf(filepath=str(source), export_format="GLB", export_animations=True)
        original_bytes = source.read_bytes()
        separate = Path(directory) / "prop.glb"
        bpy.ops.object.select_all(action="DESELECT")
        prop.select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=str(separate), export_format="GLB", use_selection=True, export_animations=False
        )
        rig = bind_parts(source, {"parts": [{"object": "Sword", "bone": "hand"}]})
        prop = bpy.data.objects["Sword"]
        assert any(mod.type == "ARMATURE" and mod.object == rig for mod in prop.modifiers)
        for vertex in prop.data.vertices:
            active = [
                (prop.vertex_groups[group.group].name, group.weight)
                for group in vertex.groups
                if group.weight > 0
            ]
            assert active == [("hand", 1.0)], active
        bpy.context.scene.frame_set(1)
        first = evaluated_points(prop)
        bpy.context.scene.frame_set(10)
        last = evaluated_points(prop)
        assert (first[0] - last[0]).length > 0.1, "Equipment did not follow animated hand"
        for index in range(1, len(first)):
            assert abs((first[index] - first[0]).length - (last[index] - last[0]).length) < 1e-5
        assert source.read_bytes() == original_bytes, "Original GLB changed"
        assert rig.animation_data and rig.animation_data.action, "Animation was lost"
        try:
            rigid_weights(prop, [100000], "hand", rig)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid vertex selection was accepted")
        # Selected armor vertices change; untouched vertices keep their prior group.
        other = prop.vertex_groups.new(name="armor")
        other.add([0, 1], 1, "REPLACE")
        rigid_weights(prop, [0], "hand", rig)
        assert prop.vertex_groups["armor"].weight(1) == 1
        rig = bind_parts(
            source,
            {
                "parts": [
                    {"source": str(separate), "bone": "hand", "offset": [0.2, 0, 0], "scale": 0.5}
                ]
            },
        )
        added = next(
            obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name != "Sword"
        )
        bpy.context.scene.frame_set(1)
        first = evaluated_points(added)
        bpy.context.scene.frame_set(10)
        last = evaluated_points(added)
        assert (first[0] - last[0]).length > 0.1
        assert abs((first[1] - first[0]).length - (last[1] - last[0]).length) < 1e-5
        repaired = Path(directory) / "repaired.glb"
        bpy.ops.export_scene.gltf(
            filepath=str(repaired), export_format="GLB", export_animations=True, export_skins=True
        )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(repaired))
        rigs = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
        assert rigs and rigs[0].animation_data and rigs[0].animation_data.action
        assert source.read_bytes() == original_bytes
        print(
            "PASS: rigid equipment follows the hand without stretching; animation and source preserved"
        )


smoke()
