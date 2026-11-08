# Model choices — researched 2026-11-08

These are selected candidates, not a measured universal ranking. Quality for
this exact armored knight needs a same-input comparison and human review.

Rechecked 2026-11-09: [Qwen Image 2.1](https://huggingface.co/Qwen/Qwen-Image-2.1)
is newer and supports native RGBA, but its
[Research License](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)
limits use to non-commercial research/evaluation unless separately licensed.
It is therefore not selected for this commercially usable game-asset workflow.
The Apache-2.0 Image Edit 2511 path remains the reference-editing choice.
The existing Hi3D result is documented in [HI3D_VALIDATION.md](HI3D_VALIDATION.md).

| Task | Choice | Why and limit |
|---|---|---|
| Reference editing / A-pose | [Qwen Image Edit 2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | Apache-2.0; multi-image editing and consistency improvements reported by the authors. Default candidate for front/back and empty-hand reconstruction. Design fidelity and anatomy are not guaranteed. |
| Faster editing alternative | [FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) | Apache-2.0, image editing and four-step distilled inference. Lower inference workload is attractive for batches; we have not compared its knight quality locally. The selected 4B license should not be generalized to every FLUX variant. |
| Background removal | [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | MIT, high-resolution foreground segmentation. Applied before editing and again to generated pictures. Thin fingers, cape edges and dark armor can still be cut incorrectly. |
| Image → textured mesh | [TRELLIS.2 4B](https://github.com/microsoft/TRELLIS.2) | MIT project with native PBR output. Produces the coarse textured source needed downstream. Single-view adapter; the separate DINOv3 encoder has its own terms. |
| Optional geometry refinement | [Hi3D 1.0](https://github.com/hi3d-official/Hi3Dv1.0) | Existing pinned open-weight refiner, not the initial image-to-3D generator. Geometry only, with scale/UV/texture restoration required afterward. Disabled by default because our previous test was not a clear quality win. |
| Body rig | [SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) via AssetFurnace | Existing humanoid auto-rig integration. Not a general wing, quadruped or rider rig. Upstream declares MIT; [license clarification issue #9](https://github.com/VAST-AI-Research/SkinTokens/issues/9) is part of the provenance review, not a legal conclusion. |
| Animation clips | Existing AssetFurnace motion presets | Reused through its public Studio API. Motion-model weights can have separate licenses (including NVIDIA Open Model License); this is not a claim that the whole dependency stack is OSI open source. |
| Other shape candidate | [TripoSG](https://github.com/VAST-AI-Research/TripoSG) | Worth comparing for shape quality, but another shape-only path does not remove our texturing/baking requirement. Not added as an untested automatic fallback. |
| Excluded default | [Hunyuan3D-2mini license](https://huggingface.co/tencent/Hunyuan3D-2mini/blob/main/LICENSE) | Tencent Community terms contain territorial restrictions including the EU. Not silently selected for this German user. |

Exact image-model and TRELLIS weight revisions are in
`asset_pipeline/models.py`; diffusers and TRELLIS source commits are pinned.
The official BiRefNet custom model code is downloaded at a pinned revision and
loaded locally with `trust_remote_code`. Hi3D and AssetFurnace have their own
existing pinned revisions. TRELLIS's DINO encoder mirror is checksum-verified;
a mirror does not replace the encoder's original license.

Next useful benchmark: same original knight, fixed seeds, Qwen versus FLUX
reference editing, then one TRELLIS 512 mesh each. Compare silhouette, empty
hands, armor, cape separation, side/back views, texture quality, rig test and
measured Modal runtime/cost. Add Hi3D only as a controlled optional comparison.
No additional benchmark cloud work was started for this change.
