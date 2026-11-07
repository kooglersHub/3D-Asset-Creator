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

APP_NAME = "3d-asset-creator"
VOLUME_NAME = "moonveil-assetfurnace-data"
VOLUME_PATH = "/af"
AF_REF = "v0.5.7"
AF_INSTALLER = (
    f"https://raw.githubusercontent.com/Bingeljell/image-to-3dlab/{AF_REF}/install.sh"
)
RUNNER_REMOTE = "/root/assetfurnace_modal_worker.py"
GPU = "L40S"  # 48 GB VRAM, cheaper GPUs can be tried after verification

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.11")
    .entrypoint([])
    .apt_install(
        "curl", "git", "ca-certificates", "build-essential", "cmake", "ninja-build",
        "pkg-config", "libgl1", "libegl1", "libglib2.0-0", "libx11-6",
        "libxi6", "libxrender1", "libxfixes3", "libxrandr2", "libsm6",
        "libxkbcommon0", "libgomp1", "libfontconfig1", "xz-utils"
    )
    .env({
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
    })
    .add_local_file(
        str(Path(__file__).with_name("assetfurnace_modal_worker.py")),
        remote_path=RUNNER_REMOTE,
    )
)

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = "/af/home/.local/bin:" + env.get("PATH", "")
    env["I3D_NO_USAGE"] = "1"
    for directory in ("home", "cache", "huggingface", "models",
                      "inputs", "exports"):
        (Path("/af") / directory).mkdir(parents=True, exist_ok=True)
    return env


def execute(*command: str, env: dict[str, str]) -> None:
    import subprocess
    print("[Modal] Executing:", " ".join(command), flush=True)
    subprocess.run(command, env=env, check=True)


@app.function(
    image=image, gpu=GPU, volumes={VOLUME_PATH: volume}, timeout=7200,
    cpu=8, memory=32768, max_containers=1
)
def setup_assetfurnace() -> str:
    """Explicit one-time download/install; partial progress survives failures."""
    env = worker_env()
    repo = Path("/af/home/image-to-3dlab")
    python = repo / ".venv/bin/python"
    try:
        execute(
            "bash", "-o", "pipefail", "-c",
            # The live installer assumes the layout of the latest release.
            f"curl -fsSL {AF_INSTALLER} | "
            f"bash -s -- --yes --no-start --ref {AF_REF} --dir {repo}",
            env=env,
        )
        if not python.is_file():
            raise RuntimeError("AssetFurnace virtualenv is absent after install.")
        for script in ("bootstrap_blender.py", "bootstrap_pixal3d.py",
                       "bootstrap_autorig.py"):
            execute(str(python), str(repo / "scripts" / script), "--yes", env=env)
        execute(str(python), str(repo / "scripts/bootstrap_autorig.py"),
                "--check", env=env)
        return "AssetFurnace, Pixal3D, Blender and SkinTokens installed."
    finally:
        volume.commit()


@app.function(
    image=image, gpu=GPU, volumes={VOLUME_PATH: volume}, timeout=7200,
    cpu=8, memory=32768, max_containers=1
)
def generate_character(
    character: str,
    input_name: str,
    input_bytes: bytes | None,
    asset_id: str,
    moves: str,
) -> tuple[str, bytes]:
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

    command = [
        str(python), RUNNER_REMOTE, "--character", character,
        "--moves", moves, "--run-name", run_name
    ]
    if source:
        command.extend(["--input", str(source)])
    else:
        command.extend(["--asset-id", asset_id])

    try:
        execute(*command, env=env)
        archive = Path("/af/exports") / (run_name + ".zip")
        if not archive.is_file():
            raise RuntimeError("Worker finished but no export ZIP exists.")
        data = archive.read_bytes()
        if len(data) > 75_000_000:
            raise RuntimeError(
                "Archive too large for direct return. It is saved at "
                + f"exports/{archive.name} in Volume {VOLUME_NAME}. "
                + "Use modal volume get to retrieve it."
            )
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
    moves: str = "idle,walk,run,sword_swing,hit_react,death",
    output: str = "",
):
    if action == "setup":
        print(setup_assetfurnace.remote())
        return
    if action != "generate":
        raise ValueError("Use action=setup or action=generate.")
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

    archive_name, archive_bytes = generate_character.remote(
        character, name, data, asset_id, moves
    )
    target = Path(output).expanduser() if output else Path.cwd() / archive_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(archive_bytes)
    print(f"Finished: {target.resolve()} ({len(archive_bytes) / 1_000_000:.1f} MB)")
    import zipfile
    with zipfile.ZipFile(target) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        print("AssetFurnace asset ID:", manifest["asset_id"])
        print("Moves:", ", ".join(manifest["moves"]))
        print("Files:", ", ".join(zipped.namelist()))
