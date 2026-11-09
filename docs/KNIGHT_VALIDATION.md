# Real knight validation — 2026-11-10

One source knight has completed the image-to-animated-Godot-package workflow.
This is a usable prototype, not an artist-finished asset or a universal model
benchmark. The other five reference characters have not been generated in this run.

## What actually ran

| Stage | Runtime and result |
|---|---|
| References | Qwen Image Edit 2511 on Modal: front/back A-pose with empty hands; separate sword. BiRefNet masks; 1024px transparent PNGs. Existing source alpha is preserved. |
| Textured geometry | TRELLIS.2 4B on Modal, 512 resolution, body plus independent sword. Body source: 155,976 triangles. The back reference is for review; this generator uses the front view. |
| Finish | Local Blender CPU, original UV/PBR-preserving decimation: body 40,000 triangles at 1.9m, sword 4,999 triangles at 1.2m. The first destructive rebake was rejected. |
| Rig | SkinTokens through AssetFurnace on Modal, 34 bones. Rig provenance matches the exact approved finished mesh. |
| Motions | Six existing NVIDIA Kimodo presets retargeted locally with AssetFurnace v0.5.7 and Blender CPU: idle, walk, run, sword_swing, hit_react, death. No local AI inference or new motion-model sampling. |
| Godot package | Single merged animated GLB, separate rigid sword, hand mapping, scene and visual controller. Walk/run horizontal root travel removed; vertical bob retained. Original separate clips retain root motion. |
| Runtime | Godot 4.6.1: all clips loaded, 34 bones, in-place locomotion, rigid hand-following sword and attack-to-idle transition passed. Native GTX 970 rendering also captured idle and attack screenshots. |

The cloud rig succeeded but the following uploaded-model export failed because
AssetFurnace did not register that upload as a generated/finished model. The worker
now retains the upload as the reviewed source and checks reusable rig provenance;
unit tests cover both cases. Further paid Modal execution was rejected by automatic
approval review, so that repaired cloud continuation has **not** been live-tested.
Read-only recovery of the existing rig allowed local completion without new compute.

## Evidence and reproducibility

- Source PNG SHA-256: `2a2fddb43015cd5951b66bdce3d4f6da04af5233a66fdc4b4f7b01a47ea0ceec`.
- Reviewed finished body SHA-256: `f69f3d49b83f8da89dc6f6e0374216e2cf0cd3b1e554bd8556ea91fca2f518aa`.
- AssetFurnace: `v0.5.7`, commit `15b1776ecd847012c8af6f32344cb763a674aa2c`.
- Reference and shape weight revisions: [model registry](../asset_pipeline/models.py).
- All six motion contact sheets inspected; idle additionally sampled at 12 points.
- Local unit tests, Blender 4.2/5.2 fixture checks, Godot fixture/runtime checks.
- Release package includes references, individual clips, rig/provenance, clean
  standalone Godot preview, runtime report, source hashes and screenshots.
- Provenance `models` fields contain the available model registry; they are not a
  claim that FLUX or Hi3D was used. Actual models used are listed above.

The real-asset Godot test is [godot_generated_asset_smoke.gd](../tests/godot_generated_asset_smoke.gd).
The automated test verifies technical behavior; the screenshots/contact sheets
support the separate visual review. Neither proves all intermediate poses are clean.

## Remaining work

- Tune cloth and armor weights, finger grip, sword alignment and animation seams
  to the game's camera and quality target. Decimation is not quad retopology.
- Add game-specific movement speeds, facing, attack hitboxes, damage windows,
  collision and death handling; test with the actual Moonveil controller.
- The isolated preview was tested. Moonveil's live project was not modified.
  Its active Godot MCP connection was left untouched; a separate preview could
  not share the already occupied localhost:6550 editor bridge.
- The original source was already transparent. Background removal is implemented
  but a new arbitrary photograph/background-to-game quality benchmark remains open.
- FLUX and higher resolutions need separately authorized comparison runs. Hi3D
  stays disabled after [its earlier comparison](HI3D_VALIDATION.md).

## Sources and terms

Use [MODEL_SELECTION.md](MODEL_SELECTION.md) for official model cards and licenses.
Reference editing is Qwen Apache-2.0, masking is BiRefNet MIT, shape generation is
TRELLIS.2 MIT with separate DINOv3 terms, rigging uses upstream SkinTokens' MIT
declaration, and AssetFurnace is Apache-2.0. The pre-generated Kimodo presets carry
their NVIDIA provenance and [NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/).
This is an open-weight/local-capable workflow with mixed terms, not a claim that
every model and dependency has an OSI open-source license. Original character art
was supplied by the user; publishing output does not establish third-party rights.
