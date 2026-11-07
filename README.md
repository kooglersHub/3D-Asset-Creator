# 3D Asset Creator

Image or GLB → reviewed static character → one motion test → animation set.
Runs AssetFurnace 0.5.7, Pixal3D, Blender and SkinTokens on Modal.
The default **stops before rigging**. A successful import is not a visual quality pass.

## Install

Python 3.11 or 3.12:

```powershell
python -m pip install -r requirements.txt
modal setup
modal run tools/modal_assetfurnace.py --action setup
```

Front/back generation needs additional weights. Install them explicitly:

```powershell
modal run tools/modal_assetfurnace.py --action setup-multiview
```

Setup and generation use a Modal L40S and incur compute charges. Model weights,
uploads and exports persist in `moonveil-assetfurnace-data` for compatibility
with the original pipeline. Storage may incur charges too: [Modal pricing](https://modal.com/pricing).
No cloud call happens when running the local preparation, review or Blender tools.

## 1. Prepare the references

```powershell
python tools/review_asset.py prepare --front references/front.png --back references/back.png --output exports/references
```

This writes a reference brief, image metadata and `reference-review.json`.
Use the brief to create or edit the artwork: one full-body character in A-pose,
empty hands, separated limbs, even lighting and matching front/back proportions.
Make weapons separately. Keep a cloak clear of the arms or make it a separate part.
This tool **does not automatically re-pose artwork or remove baked lighting**.
It cannot decide whether two views depict the same anatomy.

Inspect the pictures, then edit the review JSON: mark each check true only after
checking it and set `accepted` to true. The hashes bind this review to the files.
If a picture changes, prepare and review it again. `--back` is optional; single-view
runs still work, but cannot recover a known rear design from an unseen picture.

## 2. Make a static model and compare detail

```powershell
modal run tools/modal_assetfurnace.py --image-path references/front.png --back-image-path references/back.png --reference-review exports/references/reference-review.json --detail more --compare-details game,more --output exports/Knight-model.zip
```

Default `--stage model` exports the raw mesh, the selected finished mesh (40k
triangles requested by `more`) and a 20k comparison (`game`). Both finishes start
from the same raw model. The budget is a target, not a quality score. More triangles
cannot repair fused parts or incorrect anatomy. `--texture-size 4096` is optional;
2048 is the default. Other details: `light`, `high`, `hero`.

Instead of supplying a rear image, use `--generate-back` to let AssetFurnace draw
one. Review the resulting geometry particularly carefully: a generated rear view
is a guess, not ground truth. Both modes need the multiview setup above.
Use `--model-path character.glb` for an existing GLB, or `--asset-id ID` to create
new finish comparisons from an existing AssetFurnace asset.

The ZIP is automatically extracted beside itself, preserving existing exports.
Use `--no-extract` to keep only the ZIP. For oversized exports use the filename
printed by the worker: `modal volume get moonveil-assetfurnace-data exports/<name>.zip exports/<name>.zip`.

## 3. Inspect before rigging

With Blender 4.2+ (tested locally with 5.2):

```powershell
blender --background --python-exit-code 1 --python tools/check_blender_imports.py -- exports/Knight-model
blender --background --python-exit-code 1 --python tools/preview_asset.py -- --export exports/Knight-model
python tools/review_asset.py template --kind model --export exports/Knight-model
```

The preview renders front, side, back and three-quarter views of the raw mesh and
each finish, using neutral studio lighting and CPU rendering. `previews/report.json`
records triangle counts and filenames. Inspect silhouettes, hands, armor, holes,
fused equipment, textures and baked shadows. Change the reference or model until
acceptable, then mark the model review checks and `accepted` true.

Optional: pass `--blender-path "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"`
to a Modal generation command to run the import checks and previews automatically
after download. Automated checks never mark visual reviews accepted.

## 4. Generate one test move

Use the `asset_id` in the selected model's manifest:

```powershell
modal run tools/modal_assetfurnace.py --stage motion-test --asset-id SELECTED_MODEL_ID --model-review exports/Knight-model/model-review.json --moves idle --arms-away 8 --output exports/Knight-test.zip
blender --background --python-exit-code 1 --python tools/preview_asset.py -- --export exports/Knight-test --clips
python tools/review_asset.py template --kind motion --export exports/Knight-test
```

Only one move is allowed in this stage. Rendered samples help find problems but
are not a full playback: watch the whole clip in Blender too. Check joint bending,
feet, clipping, rigid equipment and cloth. The test export carries the validated
model review forward if AssetFurnace updates the asset ID without changing the mesh.
Accept `motion-review.json` only after viewing the motion.

## 5. Expand the reviewed character

```powershell
modal run tools/modal_assetfurnace.py --stage animate --asset-id TEST_EXPORT_ID --model-review exports/Knight-test/model-review.json --motion-review exports/Knight-test/motion-review.json --moves idle,walk,run,sword_swing,hit_react,death --arms-away 8 --output exports/Knight-animations.zip
```

Use the ID in the test export's manifest, which may differ from the static ID.
Both reviews must match the asset and exact model/test-clip hashes on the server.
Existing clips are reused. Armor clearance settings apply to every new clip.
Inspect the final clips as well: approval of idle does not guarantee a clean attack.

## Separate weapons and armor

Generate a sword as an unrigged prop, without a humanoid skeleton:

```powershell
modal run tools/modal_assetfurnace.py --stage prop --image-path references/sword.png --character Sword --compare-details "" --output exports/Sword.zip
```

`tools/bind_rigid_parts.py` can import a separate prop or rigidly weight an explicitly
selected armor mesh/vertex region to a named bone. Create a local parts JSON, e.g.:

```json
{
  "parts": [
    {"source": "Sword/model.glb", "bone": "bone_9", "offset": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1},
    {"object": "ShoulderPlate", "bone": "bone_7"}
  ]
}
```

Names and transforms above are examples: inspect the actual skeleton and align
the prop in rest space first. Source paths are relative to the parts JSON;
rotation is in radians. An existing mesh can specify `vertices: [0, 1, 2]` to repair
only a selected region. Leaving `vertices` out binds the entire named mesh.
**Do not bind an entire fused character mesh as if it were a shoulder plate.**
Automatic segmentation of a fused sword is not claimed here.

```powershell
blender --background --python-exit-code 1 --python tools/bind_rigid_parts.py -- --input exports/Knight-animations/sword_swing.glb --parts exports/parts.json --output exports/repaired/sword_swing.glb
```

The original stays untouched. Apply the same explicit selection to each compatible
clip, and review the new exports. Local repairs do not change the server asset;
cloud-generated clips need the repair reapplied. Cloth needs suitable weighting
or its own bones/simulation; rigid binding is for rigid equipment only.

## Development and validation

```powershell
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
blender --background --python-exit-code 1 --python tests/blender_equipment_smoke.py
```

Tests cover stage boundaries, stale/partial reviews, comparisons from the same
source, multiview forwarding, animation settings and rigid-equipment deformation.
CI runs offline tests/lint and a Blender GLB roundtrip test. The new staged cloud
workflow still needs its own end-to-end GPU run; unit tests do not substitute for it.

References and provenance:
- [AssetFurnace reference guidance](https://assetfurnace.com/blog/how-to-use-assetfurnace)
- [Pixel Match and front/back](https://github.com/Bingeljell/image-to-3dlab/blob/main/docs/pixel-match.md)
- [SkinTokens complex-skeleton report](https://github.com/VAST-AI-Research/SkinTokens/issues/12)
- [Pixal3D weight licenses](https://huggingface.co/raven38/pixal3d-sv-q8_0-v1)

Pipeline code only: images, generated assets and model weights are not committed.
Check the exported provenance records for the licenses applicable to your assets.
