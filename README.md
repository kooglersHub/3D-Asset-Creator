# 3D Asset Creator

Standalone pipeline for turning a character image or GLB into a skinned model
and separate animated GLBs. It runs AssetFurnace, Pixal3D, Blender and SkinTokens
on Modal. A local GPU or game project is not required.

## Setup

Install Python 3.11 or 3.12, then run from the repository root:

```powershell
python -m pip install -r requirements.txt
modal setup
modal run tools/modal_assetfurnace.py --action setup
```

Setup uses an L40S and downloads several gigabytes of model weights once.
The installation, source uploads and exports persist in the Modal volume
`moonveil-assetfurnace-data`. The existing volume name lets users of the first
version reuse their installation. Modal charges for compute and may charge for
storage; see [Modal pricing](https://modal.com/pricing).

## Generate a character

```powershell
modal run tools/modal_assetfurnace.py --image-path "C:\Images\knight.png" --character ArmoredKnight --moves idle,walk,run,sword_swing,hit_react,death --output exports/ArmoredKnight.zip
```

For best results use a transparent, full-body front image of one humanoid in
a relaxed A-pose, with the hands clear of the torso and props supplied
separately. The current pipeline uses one view and reconstructs the back.
Use `--model-path` instead to rig an existing GLB, or `--asset-id` to add clips
to a previous result.

The ZIP contains the rig, one GLB per move, a manifest and any provenance
files provided by AssetFurnace. Large ZIPs can be retrieved from the volume
using the name printed by the run:

```powershell
modal volume get moonveil-assetfurnace-data "exports/<archive>.zip" "exports/<archive>.zip"
```

## Verify an export

Extract the ZIP and pass its directory to Blender's command line:

```powershell
blender --background --python-exit-code 1 --python tools/check_blender_imports.py -- exports/ArmoredKnight
```

The check imports the rig and each clip and checks that the animated skeleton
changes pose. Review the movement visually as well, especially around armour,
cloth and handheld objects.

Run the offline source checks with `python -m unittest discover -s tests -v`.
These checks do not launch a GPU or require Modal credentials.

This repository contains pipeline code only. The reference images, assets and
model weights are not included. Check AssetFurnace's provenance records before
commercial use, particularly the licenses of the image encoder, rigging
weights and animation sources. See the
[AssetFurnace project](https://github.com/Bingeljell/image-to-3dlab) and the
[Pixal3D weight card](https://huggingface.co/raven38/pixal3d-sv-q8_0-v1).
