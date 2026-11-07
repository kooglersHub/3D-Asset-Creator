"""Render static comparisons and sampled animation poses with background Blender."""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def mesh_bounds():
    graph = bpy.context.evaluated_depsgraph_get()
    points = [
        evaluated.matrix_world @ vertex.co
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.visible_get()
        for evaluated in [obj.evaluated_get(graph)]
        for vertex in evaluated.data.vertices
    ]
    if not points:
        raise ValueError("No visible mesh in GLB")
    lower = Vector([min(point[axis] for point in points) for axis in range(3)])
    upper = Vector([max(point[axis] for point in points) for axis in range(3)])
    return (lower + upper) / 2, max(upper - lower)


def setup_scene(size, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = bpy.data.worlds.new("Neutral studio")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.3, 0.3, 0.3, 1)
    background.inputs["Strength"].default_value = 0.8
    center, extent = mesh_bounds()
    for index, offset in enumerate(((2, -3, 3), (-2, -1, 1), (1, 3, 2))):
        light = bpy.data.lights.new(f"Studio {index}", "AREA")
        light.energy = extent * extent * (100 if index == 0 else 65)
        light.size = extent * 1.5
        obj = bpy.data.objects.new(light.name, light)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(offset) * extent
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera = bpy.data.cameras.new("Review camera")
    camera.type = "ORTHO"
    camera.ortho_scale = extent * 1.25
    obj = bpy.data.objects.new(camera.name, camera)
    scene.collection.objects.link(obj)
    scene.camera = obj
    return center, extent


def render_file(source, destination, frames, size, samples):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    scene = bpy.context.scene
    actions = [
        obj.animation_data.action
        for obj in scene.objects
        if obj.type == "ARMATURE" and obj.animation_data and obj.animation_data.action
    ]
    if actions:
        first = min(action.frame_range[0] for action in actions)
        last = max(action.frame_range[1] for action in actions)
        times = [
            round(first + (last - first) * index / max(1, frames - 1)) for index in range(frames)
        ]
        views = [("front", -90)]
    else:
        times = [1]
        views = [("front", -90), ("side", 0), ("back", 90), ("threequarter", -45)]
    scene.frame_set(times[0])
    center, extent = setup_scene(size, samples)
    images = []
    for frame in times:
        scene.frame_set(frame)
        for label, angle in views:
            radians = math.radians(angle)
            camera = scene.camera
            camera.location = (
                center + Vector((3 * math.cos(radians), 3 * math.sin(radians), 0.1)) * extent
            )
            camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
            filename = f"{source.stem}-{label}-{frame:04}.png"
            scene.render.filepath = str(destination / filename)
            bpy.ops.render.render(write_still=True)
            images.append(filename)
    meshes = [obj for obj in scene.objects if obj.type == "MESH" and obj.visible_get()]
    return {
        "file": source.name,
        "images": images,
        "triangles": sum(len(poly.vertices) - 2 for obj in meshes for poly in obj.data.polygons),
        "frames": times,
        "visual_quality": "requires_human_review",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--clips", action="store_true")
    parser.add_argument("--frames", type=int, default=4)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--samples", type=int, default=16)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if min(args.frames, args.size, args.samples) < 1:
        raise ValueError("Frames, size and samples must be positive")
    directory = args.export.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    names = {"model.glb"}
    if (directory / "raw_model.glb").exists():
        names.add("raw_model.glb")
    names.update(variant["file"] for variant in manifest.get("variants", {}).values())
    if args.clips:
        names.update(f"{move}.glb" for move in manifest["moves"])
    destination = directory / "previews"
    destination.mkdir(exist_ok=True)
    rows = []
    for name in sorted(names):
        source = (directory / name).resolve()
        if not source.is_relative_to(directory):
            raise ValueError("Preview path escapes export folder")
        rows.append(render_file(source, destination, args.frames, args.size, args.samples))
    (destination / "report.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
