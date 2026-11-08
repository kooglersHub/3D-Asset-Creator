"""Blender CPU: align refinement, reduce geometry, UV unwrap and rebake PBR maps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def import_mesh(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    objects = list(set(bpy.data.objects) - before)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes or any(obj.type == "ARMATURE" for obj in objects):
        raise ValueError("Finish expects a static surface mesh, before rigging")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    mesh = bpy.context.object
    mesh.parent = None
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return mesh


def bounds(obj):
    bpy.context.view_layer.update()
    used = {index for face in obj.data.polygons for index in face.vertices}
    points = [obj.matrix_world @ obj.data.vertices[index].co for index in used]
    return (
        Vector([min(point[i] for point in points) for i in range(3)]),
        Vector([max(point[i] for point in points) for i in range(3)]),
    )


def align(high, coarse):
    low_min, low_max = bounds(coarse)
    high_min, high_max = bounds(high)
    factor = max(low_max - low_min) / max(high_max - high_min)
    high.scale *= factor
    high.location = (low_max + low_min) / 2 - (high_max + high_min) / 2 * factor
    bpy.context.view_layer.update()


def select_target(source, target):
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target


def bake_channel(source, target, material, channel, size, distance, directory):
    texture = bpy.data.images.new(channel, width=size, height=size, alpha=False)
    if channel != "Base Color":
        texture.colorspace_settings.name = "Non-Color"
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = texture
    material.node_tree.nodes.active = node
    restores = []
    try:
        if channel != "Normal":
            for source_material in dict.fromkeys(slot.material for slot in source.material_slots):
                if source_material is None or not source_material.use_nodes:
                    raise ValueError("Source materials need a Principled shader for PBR baking")
                tree = source_material.node_tree
                output = next(
                    n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output
                )
                shader = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
                if shader is None:
                    raise ValueError("Source has no Principled shader")
                original = output.inputs["Surface"].links[0].from_socket
                emission = tree.nodes.new("ShaderNodeEmission")
                socket = shader.inputs[channel]
                if socket.is_linked:
                    tree.links.new(socket.links[0].from_socket, emission.inputs["Color"])
                else:
                    value = socket.default_value
                    emission.inputs["Color"].default_value = (
                        value if channel == "Base Color" else (value, value, value, 1)
                    )
                tree.links.new(emission.outputs[0], output.inputs["Surface"])
                restores.append((tree, output, original, emission))
        select_target(source, target)
        scene = bpy.context.scene
        scene.render.bake.use_selected_to_active = True
        scene.render.bake.cage_extrusion = distance
        scene.render.bake.max_ray_distance = distance * 2
        scene.render.bake.margin = 8
        bpy.ops.object.bake(type="NORMAL" if channel == "Normal" else "EMIT")
        texture.filepath_raw = str(directory / (channel.replace(" ", "_").lower() + ".png"))
        texture.file_format = "PNG"
        texture.save()
        texture.pack()
    finally:
        for tree, output, original, emission in restores:
            tree.links.new(original, output.inputs["Surface"])
            tree.nodes.remove(emission)
    shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    if channel == "Normal":
        normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(node.outputs["Color"], normal.inputs["Color"])
        material.node_tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    else:
        material.node_tree.links.new(node.outputs["Color"], shader.inputs[channel])


def finish(coarse_path, high_path, output, triangles, texture_size, height):
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    coarse = import_mesh(coarse_path)
    high = import_mesh(high_path) if high_path else coarse
    if high_path:
        align(high, coarse)
    low = high.copy()
    low.data = high.data.copy()
    bpy.context.collection.objects.link(low)
    low.name = "CharacterSurface"
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    count = sum(len(face.vertices) - 2 for face in low.data.polygons)
    if count > triangles:
        modifier = low.modifiers.new("Game triangle budget", "DECIMATE")
        modifier.ratio = triangles / count
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    material = bpy.data.materials.new("BakedPBR")
    material.use_nodes = True
    low.data.materials.clear()
    low.data.materials.append(material)
    for face in low.data.polygons:
        face.material_index = 0
    minimum, maximum = bounds(coarse)
    distance = max(maximum - minimum) * 0.05
    if high != coarse:
        high.hide_render = True
    for channel in ("Base Color", "Roughness", "Metallic"):
        bake_channel(coarse, low, material, channel, texture_size, distance, output.parent)
    coarse.hide_render = high != coarse
    high.hide_render = False
    bake_channel(high, low, material, "Normal", texture_size, distance, output.parent)
    minimum, maximum = bounds(low)
    factor = height / (maximum.z - minimum.z)
    for vertex in low.data.vertices:
        world = low.matrix_world @ vertex.co
        vertex.co = Vector(
            (
                (world.x - (minimum.x + maximum.x) / 2) * factor,
                (world.y - (minimum.y + maximum.y) / 2) * factor,
                (world.z - minimum.z) * factor,
            )
        )
    low.matrix_world = Matrix.Identity(4)
    low.data.update()
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.export_scene.gltf(
        filepath=str(output), export_format="GLB", use_selection=True, export_animations=False
    )
    report = {
        "triangles": sum(len(face.vertices) - 2 for face in low.data.polygons),
        "height_m": height,
        "texture_size": texture_size,
        "method": "decimation, smart UV, source PBR bake, high-to-low tangent normal",
        "human_review_required": True,
    }
    output.with_suffix(".json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coarse", type=Path, required=True)
    parser.add_argument("--high", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--triangles", type=int, default=40000)
    parser.add_argument("--texture-size", type=int, default=2048)
    parser.add_argument("--height", type=float, default=1.9)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    print(
        finish(args.coarse, args.high, args.output, args.triangles, args.texture_size, args.height)
    )


if __name__ == "__main__":
    main()
