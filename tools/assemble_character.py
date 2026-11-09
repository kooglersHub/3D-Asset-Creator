"""Merge compatible single-clip GLBs into one animated GLB for Godot."""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def imported_rig(path):
    previous = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = set(bpy.data.objects) - previous
    rigs = [obj for obj in imported if obj.type == "ARMATURE"]
    if len(rigs) != 1:
        raise ValueError(f"Expected one skeleton: {path}")
    return rigs[0], imported


def skeleton_signature(rig):
    return {
        bone.name: (
            bone.parent.name if bone.parent else None,
            tuple(value for row in bone.matrix_local for value in row),
        )
        for bone in rig.data.bones
    }


def compatible_skeletons(left, right):
    if left.keys() != right.keys():
        return False
    # GLB round trips introduce sub-micrometer float drift. Rounding then exact
    # equality rejects values that straddle a decimal boundary despite being equal.
    return all(
        left[name][0] == right[name][0]
        and all(
            math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-5)
            for a, b in zip(left[name][1], right[name][1])
        )
        for name in left
    )


def make_in_place(rig, action):
    roots = [bone for bone in rig.pose.bones if bone.parent is None]
    if len(roots) != 1:
        raise ValueError("In-place locomotion needs one skeleton root")
    root = roots[0]
    data = rig.animation_data
    muted = [(track, track.mute) for track in data.nla_tracks]
    for track, _ in muted:
        track.mute = True
    data.action = action
    if hasattr(data, "action_slot") and len(action.slots):
        data.action_slot = action.slots[0]
    inverse_basis = (rig.matrix_world @ root.bone.matrix_local).to_3x3().inverted()
    first, last = map(int, action.frame_range)
    samples = []
    for frame in range(first, last + 1):
        bpy.context.scene.frame_set(frame)
        samples.append((frame, root.location.copy(), rig.matrix_world @ root.matrix.translation))
    anchor = samples[0][2]
    for frame, position, world in samples:
        root.location = position + inverse_basis @ Vector(
            (anchor.x - world.x, anchor.y - world.y, 0)
        )
        root.keyframe_insert("location", frame=frame)
    for track, mute in muted:
        track.mute = mute
    return list(samples[-1][2] - anchor)


def assemble(rig_path, clips, output, hand_bone="", in_place_clips=("walk", "run")):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    base, base_objects = imported_rig(rig_path)
    signature = skeleton_signature(base)
    base.animation_data_clear()
    base.animation_data_create()
    removed_motion = {}
    for name, path in clips.items():
        other, objects = imported_rig(path)
        if not compatible_skeletons(skeleton_signature(other), signature):
            raise ValueError(f"Clip {name} has a different skeleton/rest pose")
        data = other.animation_data
        actions = []
        if data:
            if data.action:
                actions.append(data.action)
            actions.extend(
                strip.action for track in data.nla_tracks for strip in track.strips if strip.action
            )
        actions = list(dict.fromkeys(actions))
        if len(actions) != 1:
            raise ValueError(f"Clip {name} needs exactly one action, got {len(actions)}")
        action = actions[0]
        if name in in_place_clips:
            removed_motion[name] = make_in_place(other, action)
        action.name = name
        action.use_fake_user = True
        track = base.animation_data.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 1, action)
        if hasattr(strip, "action_slot") and len(action.slots):
            strip.action_slot = action.slots[0]
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    bones = [bone.name for bone in base.data.bones]
    if hand_bone == "auto":
        candidates = [
            name
            for name in bones
            if name.lower().replace("_", "").replace(":", "")
            in {"righthand", "handr", "mixamorigrighthand", "rhand"}
        ]
        if len(candidates) != 1:
            raise ValueError(f"Set weapon.hand_bone explicitly; skeleton bones: {bones}")
        hand_bone = candidates[0]
    if hand_bone and hand_bone not in bones:
        raise ValueError(f"Unknown hand bone {hand_bone}; skeleton bones: {bones}")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in base_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = base
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        use_selection=True,
        export_animations=True,
        export_animation_mode="NLA_TRACKS",
    )
    metadata = {
        "bones": bones,
        "hand_bone": hand_bone,
        "clips": list(clips),
        "in_place_clips": list(removed_motion),
        "original_root_delta_world": removed_motion,
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    spec = json.loads(args.spec.read_text())
    assemble(
        Path(spec["rig"]),
        spec["clips"],
        Path(spec["output"]),
        spec.get("hand_bone", ""),
        spec.get("in_place_clips", ["walk", "run"]),
    )


if __name__ == "__main__":
    main()
