# Batch character production

Implemented adapters: background image → masked A-pose references → textured
TRELLIS.2 model → optional Hi3D geometry refinement → Blender UV/PBR/normal bake
and triangle reduction → SkinTokens rig + one motion test → animation set → Godot
GLB/scene with a separate sword attached to a hand bone.

**Validation boundary (2026-11-10):** one real knight completed Qwen/BiRefNet
references, TRELLIS.2 body and sword, UV-preserving finish, SkinTokens rigging,
six local Kimodo-preset retargets and a Godot runtime test. An uploaded-model
export failure was repaired; its cloud resume remains untested because further
Modal execution was rejected by automatic approval review. The existing rig was
recovered and animation completed entirely on local CPU. FLUX is an untested
alternative. Hi3D remains optional after its previous 512 comparison did not show
a clear improvement. See [the actual evidence and limits](KNIGHT_VALIDATION.md).

## Set up explicitly (charged cloud infrastructure)

Install `requirements.txt` locally and authenticate Modal. After approving the new
cloud work, run:

```powershell
modal run tools/modal_references.py --action setup --editor qwen-edit
modal run tools/modal_trellis2.py --action setup
modal run tools/modal_assetfurnace.py --action setup
```

The existing AssetFurnace installation can be reused. For the alternate editor,
install `--editor flux-klein` and set `editor` in the manifest. Hi3D needs its
existing separate setup only when explicitly enabled by `refine: true`.

Model setup downloads pinned public weights; no paid image/3D provider API key
is used. Modal compute/build/storage costs still apply. References use H200 with
CPU offload; TRELLIS.2 uses A100 80GB; Hi3D uses H200; rig/motion uses L40S.
One GPU worker per provider, stage-major processing, reuse within a stage,
no automatic GPU retries, and a 10-second worker idle window limit waste.
Builds, downloads, several reference pictures, a body plus sword, and multiple
animations can all contribute to one stage-job's cost.

## Create and inspect a manifest

```powershell
python tools/batch.py init --input-dir references --manifest batch.json
python tools/batch.py plan --manifest batch.json
```

Or adapt [the example](../examples/batch.json). Image paths resolve relative to
the manifest; absolute paths work too. `humanoid` supports the standard body rig.
Use `prop` for a static gargoyle, dragon or rider+mount until a suitable custom
skeleton and motion set exist. A rider and boar should be split into independent
assets for actual animation. The tool does not invent a quadruped/wing rig.

`weapon` requests a separate sword reference and separate textured 3D asset.
Provide `weapon.image` to use an existing weapon reference instead of extracting
one with the editor. The character prompt always requests empty hands.

## Run a bounded batch, review, resume

```powershell
python tools/batch.py run --manifest batch.json --execute-cloud --max-cloud-tasks 2
python tools/batch.py status --manifest batch.json
```

Without `--execute-cloud`, run performs only eligible local stages. The default
cap is one cloud stage-job. The cap counts stage-jobs, **not dollars or individual
remote calls**. It is not a spending guarantee. Two jobs initially produce only
references, then wait for inspection. A back picture helps review consistency;
TRELLIS.2 currently conditions only on the front view, so the back is an AI guess.

Inspect the output paths printed by `status`. Review all of the following before
approving the respective stage:

| Stage | What to inspect |
|---|---|
| `reference` | One complete figure, A-pose, empty hands, separate limbs, mask edges, matching front/back and separate usable weapon. For props: correct silhouette and mask. |
| `finish` | Front/side/back previews, anatomy, armor, hands, no fused sword, triangle budget, texture/normal projection artifacts, silhouette after decimation. |
| `motion-test` | Whole test clip: joints, feet, clipping, cloth and armor deformation. Four preview frames are not a substitute for watching the GLB. |
| `animate` | All clips, duration, motion suitability and collisions. |

```powershell
python tools/batch.py approve --manifest batch.json --job DarkKnight --stage reference --note "A-pose, hands, mask and both views inspected"
python tools/batch.py run --manifest batch.json --execute-cloud --max-cloud-tasks 2
```

If an output fails visual inspection, use `reject --job ID --stage STAGE --note "reason"`.
This preserves its artifacts, invalidates dependent stages, and requires an explicit
`run --retry-failed` before more work. Completed reference images and TRELLIS
exports are also cached remotely so a later stage failure does not repeat them.

Then approve `finish`, run the single `motion-test`, approve that, run `animate`,
approve all clips, and run again for local packaging. `--job` can be repeated to
review a selected set or limit production. Each review is bound to SHA-256 of
the actual stage outputs. Source/config/model revision changes create a separate
job version. Edited or missing artifacts block downstream work. An interrupted
task becomes failed; inspect its artifacts and explicitly use `--retry-failed`
to retry into a new artifact folder. Other figures continue after one fails.
Only one local runner can own a batch database at a time.

Results live under `exports/batch/<id>/<content-key>/<stage>/`. SQLite tracks
status and reviews. Stage `result.json` records file hashes and upstream hashes.
Keep the batch folder in place: absolute local paths are recorded. Reports and
images must remain unchanged after approval; keep review notes in the database.

## Finish and Godot integration

Blender runs locally on CPU. `--blender` accepts any Blender 4.2+ executable.
The finish tool decimates while preserving TRELLIS UVs and PBR materials by
default; it is not animation-aware quad retopology. Re-baking an unchanged
layered armor mesh can produce projection artifacts and is deliberately avoided.
When Hi3D changes the geometry, the finish path creates UVs and bakes source base
color, roughness, metallic and high-to-low tangent normals. Hi3D's mesh is aligned
back into coarse-mesh space before projection. Large topology changes can still
produce bad texture projections and must be rejected during review.

The package contains `Assets/Generated/<id>/model.glb`, `character.tscn`, the
visual adapter, provenance and optional `weapon.glb`. Copy `Assets` into the
Godot project root and instance the scene. Existing gameplay controllers should
call `set_speed`, `attack`, `hit`, `die` on the visual adapter. It handles clip
playback and one-shot return to locomotion, not combat damage or hitboxes.
The weapon stays separate and follows `BoneAttachment3D` rigidly. Packaging removes
horizontal root travel from `walk` and `run` while retaining vertical bob; original
individual clips retain their root motion. Set `in_place_clips: []` to opt out.

Automatic semantic hand names and numbered SkinTokens bones are supported.
The worker reuses AssetFurnace's humanoid topology/geometry mapper and exports
`skeleton-map.json`. If it cannot identify one right hand, packaging stops with
the bone list: set `weapon.hand_bone`
explicitly. Adjust `position`, `rotation_degrees`, `scale`, and `length` after
checking the grip. Weapon origin defaults to the center of its bottom bounding
plane; arbitrary generated swords can need a manual grip offset. Direction,
root motion, attack timing, hitboxes and collisions still need a playtest in
Moonveil. The package does not overwrite that game's controller or claim that
the new character has been integrated into the live game.

## Verification without paid inference

```powershell
python -m unittest discover -s tests -v
blender --background --python-exit-code 1 --python tests/blender_finish_smoke.py
blender --background --python-exit-code 1 --python tests/blender_package_smoke.py
godot --headless --path exports/godot-smoke --editor --import
godot --headless --path exports/godot-smoke --script res://test.gd
```

The fixture verifies actual PBR pixel output, geometry reduction, meter scale,
three clips with the same rest skeleton, in-place walking with vertical bob,
Godot import, rigid hand-following sword,
and return to idle after attacking. CI repeats this on Blender 4.2 and Godot 4.6.1.
Locally validated on Blender 5.2.3 and Godot 4.6.1; MCP server/addon 4.1.11 were
connected on localhost and their versions matched for the isolated fixture.

## Recover an existing rig locally

If a paid rig computation succeeded but a later export failed, recover the GLB
and its sibling `rigged.provenance.json`. The provenance must bind the rig to the
exact reviewed finished model. Obtain AssetFurnace's pinned `v0.5.7` source,
including its motion NPZ files (commit `15b1776ecd847012c8af6f32344cb763a674aa2c`).
Keep the upstream license and preset provenance with those files.

```powershell
python tools/batch.py run --manifest batch.json --job DarkKnight --local-rig recovered/rigged.glb --motion-repo ../image-to-3dlab --max-cloud-tasks 0 --retry-failed
```

Only motion stages use this CPU path. It never falls back to Modal on failure.
The rig, retargeter, preset metadata and preset bytes are checksum-bound to the
motion review. Approval gates still apply between the single test and the full set.
These are pre-generated Kimodo motions retargeted with Blender, not newly inferred
text-to-motion clips or fingers automatically closed around the sword.

Create an isolated, interactive Godot preview without modifying your game:

```powershell
python tools/create_preview_project.py --package PATH_TO_PACKAGE --character DarkKnight --output exports/knight-preview
godot --path exports/knight-preview --editor --import
godot --path exports/knight-preview
```

Keys: 1 idle, 2 walk, 3 run, Space attack, H hit, D death, R reset. To verify a real
package, copy `tests/godot_generated_asset_smoke.gd` into that project as `test.gd`
and run `godot --headless --path exports/knight-preview --script res://test.gd`.
