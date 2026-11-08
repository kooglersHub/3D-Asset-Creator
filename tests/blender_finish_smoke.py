"""Verify real Blender PBR baking, height normalization and texture-preserving export."""

import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from finish_character import bounds, finish


def smoke():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16)
        source = bpy.context.object
        material = bpy.data.materials.new("RedMetal")
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (0.8, 0.02, 0.01, 1)
        shader.inputs["Metallic"].default_value = 0.7
        shader.inputs["Roughness"].default_value = 0.3
        image = bpy.data.images.new("SourcePattern", width=64, height=64)
        image.pixels = [
            channel
            for y in range(64)
            for x in range(64)
            for channel in ((0.8 if (x // 8 + y // 8) % 2 else 0.4), 0.02, 0.01, 1)
        ]
        image.pack()
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
        source.data.materials.append(material)
        coarse = root / "coarse.glb"
        bpy.ops.export_scene.gltf(filepath=str(coarse), export_format="GLB")
        report = finish(coarse, coarse, root / "finished/model.glb", 300, 64, 1.9)
        assert report["triangles"] <= 302, report
        pixels = list(bpy.data.images["Base Color"].pixels)
        colored = [
            pixels[index : index + 3] for index in range(0, len(pixels), 4) if pixels[index] > 0.1
        ]
        assert colored and all(red > green * 2 for red, green, blue in colored)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(root / "finished/model.glb"))
        mesh = next(obj for obj in bpy.data.objects if obj.type == "MESH")
        minimum, maximum = bounds(mesh)
        assert abs(maximum.z - minimum.z - 1.9) < 0.001, (
            tuple(minimum),
            tuple(maximum),
            mesh.matrix_world[:],
        )
        assert abs(minimum.z) < 0.001
        assert len([image for image in bpy.data.images if image.size[0] == 64]) >= 3
        preserved = finish(coarse, None, root / "preserved/model.glb", 300, 64, 1.9)
        assert preserved["source_uvs_preserved"]
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(root / "preserved/model.glb"))
        mesh = next(obj for obj in bpy.data.objects if obj.type == "MESH")
        tree = mesh.data.materials[0].node_tree
        textures = [node.image for node in tree.nodes if node.type == "TEX_IMAGE"]
        assert textures and textures[0].size[0] == 64
        reds = list(textures[0].pixels)[::4]
        assert max(reds) - min(reds) > 0.2, "Source texture pattern was lost"
        print("PBR rebake, source texture preservation, triangle budget and meter scale passed")


smoke()
