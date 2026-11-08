"""Batch PNG -> GLB using TRELLIS.2 on Modal GPUs.

Explicit setup: modal run tools/modal_trellis2.py --action setup
Generation and resumable batches: python tools/batch.py --help

Requires RGBA PNGs with a transparent background. The first invocation will
compile GPU extensions (charged) and download TRELLIS.2 weights.
"""

from __future__ import annotations

import io
import hashlib
import json
import sys
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
APP_NAME = "asset-creator-trellis2"
MODEL_VOLUME = "darkfantasy-trellis2-weights"
MODEL_ID = "microsoft/TRELLIS.2-4B"
MODEL_REVISION = "af44b45f2e35a493886929c6d786e563ec68364d"

# Pinned mirror of the DINOv3 image encoder. This avoids requiring access to
# the gated facebook/dinov3-vitl16-pretrain-lvd1689m HF repository.
DINO_REPO = "camenduru/dinov3-vitl16-pretrain-lvd1689m"
DINO_REVISION = "3c276edd87d6f6e569ff0c4400e086807d0f3881"
DINO_SHA256 = "dcb2e45127cccbf1601e5f42fef165eea275c8e5213197e8dcf3f48822718179"
DINO_DIR = "/opt/dinov3"

image = (
    modal.Image.from_dockerfile(str(HERE / "Trellis.Dockerfile"), context_dir=str(HERE))
    .run_commands("python3.10 -m pip install --no-cache-dir 'setuptools>=64' wheel")
    .run_commands(
        "bash -lc 'cd /root/TRELLIS.2 && "
        ". ./setup.sh --cumesh --o-voxel --flexgemm --nvdiffrast --nvdiffrec'",
        gpu="A100-80GB",
        env={"TORCH_CUDA_ARCH_LIST": "8.0"},
    )
    .pip_install("transformers==4.57.5")
    .run_commands(
        f'python3.10 -c "from huggingface_hub import snapshot_download; '
        f"snapshot_download('{DINO_REPO}', revision='{DINO_REVISION}', local_dir='{DINO_DIR}')\"",
        f"echo '{DINO_SHA256}  {DINO_DIR}/model.safetensors' | sha256sum -c -",
    )
    .env(
        {
            "HF_HOME": "/models",
            "HUGGINGFACE_HUB_CACHE": "/models/hub",
            "TORCH_HOME": "/models/torch",
            "HF_XET_HIGH_PERFORMANCE": "1",
            "OPENCV_IO_ENABLE_OPENEXR": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            "ATTN_BACKEND": "sdpa",
            "SPARSE_ATTN_BACKEND": "xformers",
            "FLEX_GEMM_AUTOTUNE_CACHE_PATH": "/models/.flex_gemm/autotune_cache.json",
        }
    )
)

app = modal.App(APP_NAME)
weights = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)
_pipeline = None


def _get_pipeline():
    """Initialize model only once per warm GPU container."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    from huggingface_hub import snapshot_download
    import numpy as np
    import torch

    sys.path.insert(0, "/root/TRELLIS.2")
    from trellis2.pipelines import Trellis2ImageTo3DPipeline, rembg

    # We require an actual alpha-masked image. No background-removal model needed.
    class MaskAlreadyPresent:
        def __init__(self, *args, **kwargs):
            pass

        def to(self, device):
            return self

        def cuda(self):
            return self

        def cpu(self):
            return self

        def __call__(self, image):
            rgba = image.convert("RGBA")
            if np.all(np.asarray(rgba)[:, :, 3] == 255):
                raise ValueError("PNG ohne transparenten Hintergrund. Alpha-Maske benoetigt.")
            return rgba

    rembg.BiRefNet = MaskAlreadyPresent
    torch.backends.cudnn.enabled = False
    Path("/models/.flex_gemm").mkdir(parents=True, exist_ok=True)

    print("Lade TRELLIS.2-Gewichte (nur beim ersten Mal) ...", flush=True)
    snapshot = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_files_only=True))
    model_dir = Path("/models") / ("trellis2_" + MODEL_REVISION)
    model_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_link = model_dir / "ckpts"
    if not checkpoint_link.exists():
        checkpoint_link.symlink_to(snapshot / "ckpts", target_is_directory=True)

    config = json.loads((snapshot / "pipeline.json").read_text(encoding="utf-8"))
    config["args"]["image_cond_model"]["args"]["model_name"] = DINO_DIR
    (model_dir / "pipeline.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    weights.commit()

    print("Initialisiere GPU-Pipeline ...", flush=True)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(model_dir))
    pipeline.cuda()
    _pipeline = pipeline
    return pipeline


@app.function(
    image=image,
    gpu="A100-80GB",
    volumes={"/models": weights},
    timeout=20 * 60,
    retries=0,
    scaledown_window=10,
    max_containers=1,
)
def generate_png_to_glb(
    png_data: bytes,
    resolution: int = 512,
    decimation_target: int = 50000,
    texture_size: int = 1024,
    seed: int = 42,
) -> bytes:
    """Returns one complete .glb file. All expensive work happens in Modal."""
    from PIL import Image
    import numpy as np
    import o_voxel

    if resolution not in (512, 1024):
        raise ValueError("Nur 512 oder 1024 fuer diesen Batch-Starter zugelassen")
    if not 10000 <= decimation_target <= 1000000:
        raise ValueError("decimation_target muss 10000..1000000 sein")
    if texture_size not in (1024, 2048, 4096):
        raise ValueError("texture_size muss 1024, 2048 oder 4096 sein")

    key = hashlib.sha256(
        json.dumps(
            {
                "image": hashlib.sha256(png_data).hexdigest(),
                "resolution": resolution,
                "triangles": decimation_target,
                "texture_size": texture_size,
                "seed": seed,
                "model_revision": MODEL_REVISION,
                "dino_sha256": DINO_SHA256,
                "source_revision": "75fbf0183001ed9876c8dbb35de6b68552ee08bd",
                "export_schema": 1,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cached = Path("/models/generated") / (key + ".glb")
    if cached.is_file():
        result = cached.read_bytes()
        if not result.startswith(b"glTF"):
            raise ValueError(f"Corrupt cached GLB; inspect before retry: {cached}")
        print(f"[TRELLIS.2] Reused completed model {key}", flush=True)
        return result

    image_in = Image.open(io.BytesIO(png_data)).convert("RGBA")
    alpha = np.asarray(image_in.getchannel("A"))
    if alpha.min() == 255 or alpha.max() == 0:
        raise ValueError("PNG benoetigt einen sichtbaren Gegenstand mit Alpha-Maske")

    pipeline = _get_pipeline()
    mesh = pipeline.run(
        image_in,
        seed=seed,
        pipeline_type="512" if resolution == 512 else "1024_cascade",
    )[0]
    mesh.simplify(16_777_216)  # NVIDIA nvdiffrast limit
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=decimation_target,
        texture_size=texture_size,
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=False,
    )
    # Standard glTF textures, compatible with Blender and game engines.
    # WebP export can fail on upstream pillow-simd builds without WebP support.
    file_obj = io.BytesIO()
    glb.export(file_obj, file_type="glb")
    result = file_obj.getvalue()
    if not result.startswith(b"glTF"):
        raise RuntimeError("GLB output header invalid")
    cached.parent.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".tmp")
    temporary.write_bytes(result)
    temporary.replace(cached)
    weights.commit()
    return result


@app.function(image=image, volumes={"/models": weights}, timeout=7200, retries=0)
def setup():
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID, revision=MODEL_REVISION)
    weights.commit()
    return {"model": MODEL_ID, "revision": MODEL_REVISION}


@app.local_entrypoint()
def main(action: str = "setup"):
    if action != "setup":
        raise ValueError("Use tools/batch.py for generation")
    print(setup.remote())
