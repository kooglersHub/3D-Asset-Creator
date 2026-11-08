"""Optional Hi3D 1.0 refinement on Modal. Powered by Hi3D 1.0 (https://hi3d.ai/).

Takes the SAME reference image and a static coarse GLB. Outputs geometry only.
Uses a separate CUDA 12.4 / Python 3.10 stack; never installed by normal setup.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from uuid import uuid4

import modal

from hi3d_support import (
    ATTRIBUTION,
    CODE_REVISION,
    MODEL_REVISIONS,
    geometry_manifest,
    resolve_coarse,
    validate_settings,
)
from quality_workflow import extract_export, read_json, sha256, write_json

VOLUME_NAME = "3d-asset-creator-hi3d"
CACHE = Path("/hi3d-cache")
REPO = "/opt/hi3d"
MODEL_NAMES = ("refiner", "dino", "clip")

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.10")
    .entrypoint([])
    .apt_install(
        "git", "ca-certificates", "build-essential", "ninja-build", "libgl1", "libglib2.0-0"
    )
    .env(
        {
            "TORCH_CUDA_ARCH_LIST": "9.0",
            "FLASH_ATTENTION_FORCE_BUILD": "TRUE",
            "MAX_JOBS": "4",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    .run_commands(
        f"git init {REPO}",
        f"git -C {REPO} remote add origin https://github.com/hi3d-official/Hi3Dv1.0.git",
        f"git -C {REPO} fetch --depth 1 origin {CODE_REVISION}",
        f"git -C {REPO} checkout --detach FETCH_HEAD",
        "python -m pip install --upgrade pip setuptools wheel",
        f"python -m pip install -c {REPO}/docker/constraints.txt "
        "torch==2.5.0+cu124 torchvision==0.20.0+cu124 "
        "--extra-index-url https://download.pytorch.org/whl/cu124",
        "python -m pip install packaging ninja psutil",
        f"python -m pip install -c {REPO}/docker/constraints.txt "
        "--no-build-isolation flash-attn==2.7.4.post1",
        f"python -m pip install -c {REPO}/docker/constraints.txt '{REPO}[inference]'",
    )
    .env({"HF_HOME": str(CACHE / "huggingface"), "HF_HUB_DISABLE_XET": "1"})
)
for module in ("quality_workflow.py", "hi3d_support.py"):
    image = image.add_local_file(str(Path(__file__).with_name(module)), f"/root/{module}")

app = modal.App("3d-asset-creator-hi3d")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


@app.function(image=image, volumes={str(CACHE): volume}, timeout=7200, cpu=8, memory=32768)
def setup_hi3d() -> str:
    """Explicitly download all three pinned components without reserving a GPU."""
    from huggingface_hub import snapshot_download

    try:
        for name, (repository, revision) in zip(MODEL_NAMES, MODEL_REVISIONS.items()):
            snapshot_download(
                repository,
                revision=revision,
                local_dir=str(CACHE / name),
                allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
                max_workers=2,
            )
        write_json(CACHE / "installed.json", {"revisions": MODEL_REVISIONS})
        return f"{ATTRIBUTION}: refiner and both image encoders downloaded."
    finally:
        volume.commit()


@app.function(
    image=image,
    gpu="H200",
    volumes={str(CACHE): volume},
    timeout=3600,
    cpu=8,
    memory=65536,
    max_containers=1,
)
def refine_geometry(
    reference_bytes: bytes,
    coarse_bytes: bytes,
    resolution: int,
    steps: int,
    seed: int,
    offload: bool,
) -> str:
    import hashlib
    import io
    import trimesh
    from PIL import Image
    from sparc3d import Sparc3DPipeline

    validate_settings(resolution, steps)
    if not (CACHE / "installed.json").is_file():
        raise RuntimeError("Run --action setup for Hi3D first")
    if read_json(CACHE / "installed.json")["revisions"] != MODEL_REVISIONS:
        raise RuntimeError("Hi3D weights changed; run --action setup again")
    run = "hi3d-" + uuid4().hex[:12]
    folder = CACHE / "exports" / run
    folder.mkdir(parents=True)
    reference = folder / "reference.png"
    with Image.open(io.BytesIO(reference_bytes)) as picture:
        picture.save(reference)
    coarse = folder / "raw_model.glb"
    coarse.write_bytes(coarse_bytes)

    def report(event):
        print(
            f"[Hi3D] pass {event.pass_index + 1}/{event.num_passes}, "
            f"step {event.step}/{event.num_inference_steps}",
            flush=True,
        )

    try:
        pipeline = Sparc3DPipeline.from_pretrained(
            str(CACHE / "refiner"),
            local_files_only=True,
            dino_model_id=str(CACHE / "dino"),
            clip_model_id=str(CACHE / "clip"),
            progress_callback=report,
        )
        if offload:
            pipeline.enable_sequential_cpu_offload("cuda")
        else:
            pipeline.to("cuda")
        with Image.open(reference) as picture:
            result = pipeline(
                picture,
                trimesh.load(coarse, force="mesh", process=False),
                resolution=resolution,
                num_inference_steps=steps,
                seed=seed,
            )
        if result.resolution != resolution:
            raise RuntimeError(f"Requested {resolution}, received {result.resolution}")
        result.mesh.export(folder / "model.glb")
        manifest = geometry_manifest(
            hashlib.sha256(reference_bytes).hexdigest(),
            sha256(coarse),
            resolution,
            steps,
            seed,
            offload,
        )
        manifest["hashes"] = {
            name: sha256(folder / name) for name in ("raw_model.glb", "model.glb", "reference.png")
        }
        write_json(folder / "manifest.json", manifest)
        write_json(folder / "model.provenance.json", manifest)
        archive = folder.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in folder.iterdir():
                bundle.write(file, file.name)
        print(f"{ATTRIBUTION} — geometry only; textures, scale and rig need review.")
        return f"exports/{archive.name}"
    finally:
        volume.commit()


@app.local_entrypoint()
def main(
    action: str = "refine",
    image_path: str = "",
    model_path: str = "",
    coarse_export: str = "",
    output: str = "",
    resolution: int = 512,
    steps: int = 25,
    seed: int = 0,
    offload: bool = True,
):
    if action == "setup":
        print(setup_hi3d.remote())
        return
    if action != "refine":
        raise ValueError("Use --action setup or refine")
    validate_settings(resolution, steps)
    reference = Path(image_path).expanduser()
    if not reference.is_file():
        raise FileNotFoundError("Provide --image-path with the same image used for the coarse mesh")
    coarse = resolve_coarse(model_path, coarse_export, reference)
    if max(reference.stat().st_size, coarse.stat().st_size) > 250_000_000:
        raise ValueError("Each input must be smaller than 250 MB")
    target = Path(output).expanduser() if output else Path(f"hi3d-{uuid4().hex[:12]}.zip")
    if target.suffix.lower() != ".zip" or target.exists() or target.with_suffix("").exists():
        raise ValueError("Choose a new .zip output path")
    remote = refine_geometry.remote(
        reference.read_bytes(), coarse.read_bytes(), resolution, steps, seed, offload
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".zip.part")
    with partial.open("xb") as downloaded:
        for chunk in volume.read_file(remote):
            downloaded.write(chunk)
    partial.rename(target)
    extract_export(target, target.with_suffix(""))
    print(f"{ATTRIBUTION}: {target.resolve()}")
    print("Compare in Blender with tools/preview_asset.py. Add textures before character rigging.")
