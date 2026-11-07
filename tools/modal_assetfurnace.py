#!/usr/bin/env python3
"""Standalone image/GLB to animated character GLBs on Modal GPU.

Run:
  modal run tools/modal_assetfurnace.py --action setup
  modal run tools/modal_assetfurnace.py --image-path C:/images/knight.png
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import modal

from quality_workflow import check_review, extract_export, read_json, sha256, validate_options

APP_NAME = "3d-asset-creator"
VOLUME_NAME = "moonveil-assetfurnace-data"
VOLUME_PATH = "/af"
AF_REF = "v0.5.7"
AF_INSTALLER = f"https://raw.githubusercontent.com/Bingeljell/image-to-3dlab/{AF_REF}/install.sh"
RUNNER_REMOTE = "/root/assetfurnace_modal_worker.py"
GPU = "L40S"  # 48 GB VRAM, cheaper GPUs can be tried after verification

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.11")
    .entrypoint([])
    .apt_install(
        "curl",
        "git",
        "ca-certificates",
        "build-essential",
        "cmake",
        "ninja-build",
        "pkg-config",
        "libgl1",
        "libegl1",
        "libglib2.0-0",
        "libx11-6",
        "libxi6",
        "libxrender1",
        "libxfixes3",
        "libxrandr2",
        "libsm6",
        "libxkbcommon0",
        "libgomp1",
        "libfontconfig1",
        "xz-utils",
    )
    .env(
        {
            "HOME": "/af/home",
            "XDG_CACHE_HOME": "/af/cache",
            "HF_HOME": "/af/huggingface",
            "ASSETFURNACE_MODELS": "/af/models",
            # Setup writes here; Studio otherwise looks under ASSETFURNACE_MODELS/u2net.
            "U2NET_HOME": "/af/home/.u2net",
            # uv creates locks and hard links the Modal Volume cannot provide.
            "UV_CACHE_DIR": "/tmp/assetfurnace-uv-cache",
            "UV_LINK_MODE": "copy",
            "HF_HUB_DISABLE_XET": "1",
        }
    )
    .add_local_file(
        str(Path(__file__).with_name("assetfurnace_modal_worker.py")),
        remote_path=RUNNER_REMOTE,
    )
)

image = image.add_local_file(
    str(Path(__file__).with_name("quality_workflow.py")),
    remote_path="/root/quality_workflow.py",
)

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = "/af/home/.local/bin:" + env.get("PATH", "")
    env["I3D_NO_USAGE"] = "1"
    for directory in ("home", "cache", "huggingface", "models", "inputs", "exports"):
        (Path("/af") / directory).mkdir(parents=True, exist_ok=True)
    return env


def execute(*command: str, env: dict[str, str]) -> None:
    import subprocess

    print("[Modal] Executing:", " ".join(command), flush=True)
    subprocess.run(command, env=env, check=True)


@app.function(
    image=image,
    gpu=GPU,
    volumes={VOLUME_PATH: volume},
    timeout=7200,
    cpu=8,
    memory=32768,
    max_containers=1,
)
def setup_assetfurnace(multiview: bool = False) -> str:
    """Explicit one-time download/install; partial progress survives failures."""
    env = worker_env()
    repo = Path("/af/home/image-to-3dlab")
    python = repo / ".venv/bin/python"
    try:
        execute(
            "bash",
            "-o",
            "pipefail",
            "-c",
            # The live installer assumes the layout of the latest release.
            f"curl -fsSL {AF_INSTALLER} | bash -s -- --yes --no-start --ref {AF_REF} --dir {repo}",
            env=env,
        )
        if not python.is_file():
            raise RuntimeError("AssetFurnace virtualenv is absent after install.")
        for script in ("bootstrap_blender.py", "bootstrap_pixal3d.py", "bootstrap_autorig.py"):
            execute(str(python), str(repo / "scripts" / script), "--yes", env=env)
        execute(str(python), str(repo / "scripts/bootstrap_autorig.py"), "--check", env=env)
        if multiview:
            execute(str(python), str(repo / "scripts/bootstrap_front_back.py"), "--yes", env=env)
        return "AssetFurnace setup complete."
    finally:
        volume.commit()


@app.function(
    image=image,
    gpu=GPU,
    volumes={VOLUME_PATH: volume},
    timeout=7200,
    cpu=8,
    memory=32768,
    max_containers=1,
)
def generate_character(
    character: str,
    input_name: str,
    input_bytes: bytes | None,
    asset_id: str,
    moves: str,
    options: dict,
    back_name: str = "",
    back_bytes: bytes | None = None,
) -> tuple[str, bytes | None]:
    env = worker_env()
    repo = Path("/af/home/image-to-3dlab")
    python = repo / ".venv/bin/python"
    if not python.is_file():
        raise RuntimeError("First run modal with --action setup.")

    run_name = "asset-" + uuid4().hex[:12]
    source = None
    if input_bytes is not None:
        extension = Path(input_name).suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".glb"}:
            raise ValueError("Supported source types: PNG, JPG, WebP, GLB.")
        if len(input_bytes) > 75_000_000:
            raise ValueError("Source is larger than 75 MB.")
        source = Path("/af/inputs") / (run_name + extension)
        source.write_bytes(input_bytes)
    elif not asset_id:
        raise ValueError("Specify an input image/GLB or AssetFurnace asset_id.")

    back_source = None
    if back_bytes is not None:
        extension = Path(back_name).suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp"} or len(back_bytes) > 75_000_000:
            raise ValueError("Back view must be an image smaller than 75 MB")
        back_source = Path("/af/inputs") / (run_name + "-back" + extension)
        back_source.write_bytes(back_bytes)

    command = [
        str(python),
        RUNNER_REMOTE,
        "--character",
        character,
        "--moves",
        moves,
        "--run-name",
        run_name,
        "--options-json",
        json.dumps(options),
    ]
    if source:
        command.extend(["--input", str(source)])
    else:
        command.extend(["--asset-id", asset_id])

    if back_source:
        command.extend(["--back-input", str(back_source)])
    try:
        execute(*command, env=env)
        archive = Path("/af/exports") / (run_name + ".zip")
        if not archive.is_file():
            raise RuntimeError("Worker finished but no export ZIP exists.")
        # Raw/detail comparisons can be large. Stream those from the committed
        # Volume on the client instead of sending them back through the GPU worker.
        data = archive.read_bytes() if archive.stat().st_size <= 75_000_000 else None
        return archive.name, data
    finally:
        volume.commit()


@app.local_entrypoint()
def main(
    action: str = "generate",
    character: str = "ArmoredKnight",
    image_path: str = "",
    model_path: str = "",
    asset_id: str = "",
    moves: str = "idle",
    output: str = "",
    stage: str = "model",
    back_image_path: str = "",
    generate_back: bool = False,
    detail: str = "more",
    compare_details: str = "game,more",
    texture_size: int = 2048,
    arms_away: float = 8,
    reference_review: str = "",
    model_review: str = "",
    motion_review: str = "",
    extract: bool = True,
    blender_path: str = "",
):
    if action in {"setup", "setup-multiview"}:
        print(setup_assetfurnace.remote(multiview=action == "setup-multiview"))
        return
    if action != "generate":
        raise ValueError("Use action=setup, setup-multiview or generate.")
    if output:
        target = Path(output).expanduser()
        if target.suffix.lower() != ".zip":
            raise ValueError("Output must be a .zip file")
        if target.exists() or (extract and target.with_suffix("").exists()):
            raise FileExistsError(f"Choose a new output path: {target}")
    if blender_path and not Path(blender_path).is_file():
        raise FileNotFoundError(blender_path)
    provided = [bool(image_path), bool(model_path), bool(asset_id)]
    if sum(provided) != 1:
        raise ValueError("Exactly one of --image-path, --model-path, --asset-id required.")
    character = character.strip()
    if not character:
        raise ValueError("Provide a character name.")
    data = None
    name = ""
    if image_path or model_path:
        source = Path(image_path or model_path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)
        name, data = source.name, source.read_bytes()

    if stage in {"motion-test", "animate"} and not asset_id:
        raise ValueError("Animation requires a reviewed --asset-id, not a new source")
    options = {
        "stage": stage,
        "detail": detail,
        "moves": moves,
        "generate_back": generate_back,
        "texture_size": texture_size,
        "arms_away": arms_away,
        "compare_details": [value.strip() for value in compare_details.split(",") if value.strip()]
        if stage in {"model", "prop"}
        else [],
        "reviews": {
            kind: read_json(Path(path))
            for kind, path in (
                ("reference", reference_review),
                ("model", model_review),
                ("motion", motion_review),
            )
            if path
        },
    }
    validate_options(options, has_image=bool(image_path), has_back=bool(back_image_path))
    back = Path(back_image_path).expanduser() if back_image_path else None
    if image_path and stage == "model":
        hashes = {"front": sha256(source)}
        if back:
            hashes["back"] = sha256(back)
        check_review(options["reviews"].get("reference", {}), "reference", hashes)
    if stage in {"motion-test", "animate"}:
        review = options["reviews"].get("model", {})
        check_review(review, "model", review.get("hashes", {}), asset_id)
        if stage == "animate":
            review = options["reviews"].get("motion", {})
            check_review(review, "motion", review.get("hashes", {}), asset_id)
    archive_name, archive_bytes = generate_character.remote(
        character,
        name,
        data,
        asset_id,
        moves,
        options,
        back.name if back else "",
        back.read_bytes() if back else None,
    )
    target = Path(output).expanduser() if output else Path.cwd() / archive_name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as downloaded:
        if archive_bytes is not None:
            downloaded.write(archive_bytes)
        else:
            for chunk in volume.read_file(f"exports/{archive_name}"):
                downloaded.write(chunk)
    print(f"Finished: {target.resolve()} ({target.stat().st_size / 1_000_000:.1f} MB)")
    import zipfile

    with zipfile.ZipFile(target) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        print("AssetFurnace asset ID:", manifest["asset_id"])
        print("Moves:", ", ".join(manifest["moves"]))
        print("Files:", ", ".join(zipped.namelist()))
    if extract:
        directory = target.with_suffix("")
        extract_export(target, directory)
        print("Extracted:", directory.resolve())
        if blender_path:
            tools = Path(__file__).resolve().parent
            execute(
                blender_path,
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(tools / "check_blender_imports.py"),
                "--",
                str(directory.resolve()),
                env=os.environ.copy(),
            )
            preview = [
                blender_path,
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(tools / "preview_asset.py"),
                "--",
                "--export",
                str(directory.resolve()),
            ]
            if manifest["moves"]:
                preview.append("--clips")
            execute(*preview, env=os.environ.copy())
