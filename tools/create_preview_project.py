"""Make an isolated Godot review project from a generated package."""

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--character", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.character):
        raise ValueError("Invalid character ID")
    output = args.output.resolve()
    if (output / "project.godot").exists() and not (output / "asset-preview.json").exists():
        raise FileExistsError("Preserve the existing Godot project")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.package / "Assets", output / "Assets", dirs_exist_ok=True)
    shutil.copy2(ROOT / "godot/preview_character.gd", output / "preview.gd")
    (output / "project.godot").write_text("""config_version=5
[application]
config/name="Generated Character Review"
run/main_scene="res://preview.tscn"
[display]
window/size/viewport_width=960
window/size/viewport_height=720
[rendering]
renderer/rendering_method="gl_compatibility"
textures/default_filters/use_nearest_mipmap_filter=false
""")
    scene = """[gd_scene load_steps=7 format=3]
[ext_resource type="Script" path="res://preview.gd" id="1"]
[ext_resource type="PackedScene" path="res://Assets/Generated/CHARACTER/character.tscn" id="2"]
[sub_resource type="Environment" id="Environment"]
background_mode = 1
background_color = Color(0.06, 0.07, 0.1, 1)
ambient_light_source = 2
ambient_light_color = Color(0.6, 0.65, 0.8, 1)
ambient_light_energy = 0.7
[sub_resource type="StandardMaterial3D" id="GroundMaterial"]
albedo_color = Color(0.12, 0.14, 0.18, 1)
roughness = 0.9
[sub_resource type="PlaneMesh" id="Ground"]
material = SubResource("GroundMaterial")
size = Vector2(8, 8)
[sub_resource type="LabelSettings" id="LabelSettings"]
font_size = 20
font_color = Color(0.9, 0.92, 1, 1)
outline_size = 4
outline_color = Color(0.04, 0.05, 0.08, 1)
[node name="Review" type="Node3D"]
script = ExtResource("1")
character_scene = ExtResource("2")
[node name="WorldEnvironment" type="WorldEnvironment" parent="."]
environment = SubResource("Environment")
[node name="Ground" type="MeshInstance3D" parent="."]
mesh = SubResource("Ground")
[node name="KeyLight" type="DirectionalLight3D" parent="."]
rotation_degrees = Vector3(-35, -35, 0)
light_energy = 2.0
shadow_enabled = true
[node name="FillLight" type="DirectionalLight3D" parent="."]
rotation_degrees = Vector3(-20, 135, 0)
light_energy = 0.8
light_specular = 0.0
[node name="Camera3D" type="Camera3D" parent="."]
position = Vector3(2.7, 1.6, 3.8)
current = true
fov = 42.0
[node name="CanvasLayer" type="CanvasLayer" parent="."]
[node name="Label" type="Label" parent="CanvasLayer"]
offset_left = 20.0
offset_top = 18.0
label_settings = SubResource("LabelSettings")
"""
    (output / "preview.tscn").write_text(scene.replace("CHARACTER", args.character))
    (output / "asset-preview.json").write_text(
        json.dumps(
            {"character": args.character, "source_package": str(args.package.resolve())}, indent=2
        )
    )


if __name__ == "__main__":
    main()
