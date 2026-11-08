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

## Optional Hi3D 1.0 geometry refinement

[Powered by Hi3D 1.0](https://hi3d.ai/).

Hi3D 1.0 is a **refiner**, not the A-pose editor or an automatic replacement for
the textured-character pipeline. It takes an existing static coarse mesh and the
same reference image. Use Pixal3D for the coarse model, then compare a Hi3D result.
The original input and existing AssetFurnace installation are preserved.

Explicit one-time setup, separate from the normal setup:

```powershell
modal run tools/modal_hi3d.py --action setup
```

Then refine the raw mesh in a static export:

```powershell
modal run tools/modal_hi3d.py --image-path references/front.png --coarse-export exports/Knight-model --resolution 512 --output exports/Knight-hi3d.zip
```

Or supply `--model-path coarse.glb` instead of `--coarse-export`. The export form
checks the reference hash when the original generation recorded it and rejects
animated inputs. A manually supplied GLB must be unrigged and match the image.

The ZIP is extracted and supports the existing preview and static import checks:

```powershell
blender --background --python-exit-code 1 --python tools/preview_asset.py -- --export exports/Knight-hi3d
blender --background --python-exit-code 1 --python tools/check_blender_imports.py -- exports/Knight-hi3d
```

Important differences:
- Geometry only: no UV textures, materials, colors, rig or animation are generated.
  Transfer or create textures in Blender, then import the textured result through
  `modal_assetfurnace.py --model-path ...` for a fresh static review.
- Hi3D recenters and normalizes the result; original world scale and origin are not
  preserved. Align it to the coarse mesh before texture transfer or equipment work.
- Structural errors and pose problems in the coarse input can persist. Hi3D does
  not turn a posed character into an A-pose.
- The adapter uses its own pinned source/model revisions, Python 3.10 and CUDA 12.4
  image, with a separate `3d-asset-creator-hi3d` volume. It follows the upstream
  PyTorch/kernel constraints. Building flash-attn can take considerable time.
- Inference requests a Modal H200; this is a separate, more expensive GPU choice
  than the existing L40S workflow. The default starts at 512, with sequential CPU
  offload. `--resolution 1024` and `1536` are optional. The upstream measured the
  high-resolution path on a 96 GB H20; this H200 adapter is **not GPU-validated**.
- Setup downloads the refiner, DINOv2 and CLIP explicitly. It reserves no GPU, but
  CPU builds, downloads, storage and later inference may incur Modal charges.

The code and all three model revisions are recorded in the manifest together with
source hashes, normalization and texture limitations. Geometry-only output cannot
receive a character model approval through the review tool.
The [Hi3D license](https://github.com/hi3d-official/Hi3Dv1.0/blob/13657ad09afaa92e350b461a1cd66399e44a59c4/LICENSE)
is a custom license; retain its notices and integration attribution requirements.
See the [official model card](https://huggingface.co/hi3d-official/Hi3Dv1.0)
for installation requirements, scope and hardware validation.

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
