"""Pinned open-weight reference editing and masking. Importing never starts a GPU."""

from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
app = modal.App("asset-creator-references")
weights = modal.Volume.from_name("asset-creator-reference-weights", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install(
        "torch==2.8.0",
        "torchvision==0.23.0",
        "transformers==4.57.5",
        "accelerate==1.11.0",
        "huggingface-hub==0.36.2",
        "Pillow==12.0.0",
        "timm==1.0.22",
        "einops==0.8.1",
        "safetensors==0.6.2",
        "diffusers==0.37.0",
    )
    .pip_install("kornia==0.8.2")
    .env({"HF_HOME": "/weights/huggingface", "HF_HUB_DISABLE_TELEMETRY": "1"})
    .add_local_dir(str(ROOT / "asset_pipeline"), remote_path="/root/asset_pipeline")
)
_editors = {}
_masker = None


@app.function(image=image, volumes={"/weights": weights}, timeout=7200, retries=0)
def setup(editor: str = "qwen-edit"):
    import os
    from huggingface_hub import snapshot_download
    from asset_pipeline.models import MODELS

    if editor not in {"qwen-edit", "flux-klein"}:
        raise ValueError("Choose qwen-edit or flux-klein")
    os.environ["HF_HUB_OFFLINE"] = "0"
    for name in (editor, "birefnet"):
        model = MODELS[name]
        snapshot_download(model["repo"], revision=model["revision"], local_dir=f"/weights/{name}")
        Path(f"/weights/{name}/installed.json").write_text(json.dumps(model))
    weights.commit()
    return {"installed": editor, "masker": "birefnet"}


def get_masker():
    global _masker
    from transformers import AutoModelForImageSegmentation
    from asset_pipeline.models import MODELS

    directory = Path("/weights/birefnet")
    if json.loads((directory / "installed.json").read_text()) != MODELS["birefnet"]:
        raise RuntimeError("Run reference setup for the pinned BiRefNet revision")
    if _masker is None:
        # The official repository includes custom model code; setup pins that code too.
        _masker = (
            AutoModelForImageSegmentation.from_pretrained(
                str(directory), trust_remote_code=True, local_files_only=True
            )
            .eval()
            .cpu()
        )
    return _masker


def mask_background(picture):
    if "A" in picture.getbands():
        alpha_min, alpha_max = picture.getchannel("A").getextrema()
        if alpha_max == 0:
            raise ValueError("Source image is fully transparent")
        if alpha_min < 250:
            # Preserve artist-provided dark armor and thin fingers.
            return picture.convert("RGBA")
    import torch
    from torchvision import transforms

    masker = get_masker()
    original = picture.convert("RGB")
    transform = transforms.Compose(
        [
            transforms.Resize((1024, 1024)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    with torch.inference_mode():
        prediction = masker(transform(original).unsqueeze(0))[-1].sigmoid().squeeze().cpu()
    alpha = transforms.ToPILImage()(prediction).resize(original.size)
    if alpha.getbbox() is None or alpha.getextrema()[0] >= 250:
        raise ValueError("Background removal produced no usable foreground mask")
    original.putalpha(alpha)
    return original


def edit(pictures, prompt, editor, seed):
    import gc
    import torch
    from diffusers import Flux2KleinPipeline, QwenImageEditPlusPipeline
    from asset_pipeline.models import MODELS

    directory = Path(f"/weights/{editor}")
    if json.loads((directory / "installed.json").read_text()) != MODELS[editor]:
        raise RuntimeError(f"Run reference setup for {editor}")
    if editor not in _editors:
        # One editor per container avoids retaining two very large GPU models.
        _editors.clear()
        gc.collect()
        torch.cuda.empty_cache()
        pipeline_class = QwenImageEditPlusPipeline if editor == "qwen-edit" else Flux2KleinPipeline
        pipeline = pipeline_class.from_pretrained(
            str(directory), torch_dtype=torch.bfloat16, local_files_only=True
        )
        if torch.cuda.get_device_properties(0).total_memory >= 96 * 1024**3:
            pipeline.to("cuda")
        else:
            pipeline.enable_model_cpu_offload()
        _editors[editor] = pipeline
    parameters = {
        "image": pictures,
        "prompt": prompt,
        "generator": torch.Generator(device="cuda").manual_seed(seed),
    }
    if editor == "qwen-edit":
        parameters.update(
            true_cfg_scale=4.0, guidance_scale=1.0, negative_prompt=" ", num_inference_steps=40
        )
    else:
        parameters.update(guidance_scale=1.0, num_inference_steps=4, width=1024, height=1024)
    return _editors[editor](**parameters).images[0]


def cached_edit(directory, name, pictures, prompt, editor, seed):
    from PIL import Image

    path = directory / f"{name}.png"
    if path.is_file():
        with Image.open(path) as picture:
            return picture.copy()
    result = edit(pictures, prompt, editor, seed)
    temporary = path.with_suffix(".tmp")
    result.save(temporary, format="PNG")
    temporary.replace(path)
    weights.commit()
    print(f"[References] Saved {name} checkpoint", flush=True)
    return result


@app.function(
    image=image,
    gpu="H200",
    volumes={"/weights": weights},
    cpu=8,
    memory=65536,
    timeout=3600,
    retries=0,
    max_containers=1,
    scaledown_window=10,
)
def generate(source: bytes, config: dict, weapon: bytes | None = None) -> dict[str, bytes]:
    from PIL import Image
    from asset_pipeline.models import FRONT_PROMPT, BACK_PROMPT, WEAPON_PROMPT, MODELS

    key = hashlib.sha256(
        json.dumps(
            {
                "source": hashlib.sha256(source).hexdigest(),
                "config": config,
                "models": MODELS,
                "weapon": hashlib.sha256(weapon).hexdigest() if weapon else None,
                "prompts": [FRONT_PROMPT, BACK_PROMPT, WEAPON_PROMPT],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache = Path("/weights/reference-runs") / key
    cache.mkdir(parents=True, exist_ok=True)
    # Validate custom model imports before spending GPU time on edited images.
    get_masker()

    original = mask_background(Image.open(io.BytesIO(source)))
    white = Image.new("RGB", original.size, "white")
    white.paste(original, mask=original.getchannel("A"))
    seed, editor = config.get("seed", 42), config.get("editor", "qwen-edit")
    if config.get("kind", "humanoid") == "prop":
        outputs = {"front": original}
    else:
        front = cached_edit(cache, "front", [white], FRONT_PROMPT, editor, seed)
        back = cached_edit(cache, "back", [white, front], BACK_PROMPT, editor, seed + 1)
        outputs = {"front": mask_background(front), "back": mask_background(back)}
    if config.get("weapon"):
        weapon_prompt = WEAPON_PROMPT + (
            " Match the source image's realistic rendered 3D game-asset style exactly. "
            "Weathered dark steel with physically based metallic reflections, bevels, "
            "subtle blue highlights, worn leather grip and fine surface detail. "
            "A studio product render of a real three-dimensional sword, not a drawing. "
            "No cartoon outlines, no cel shading, no flat illustration."
        )
        weapon_image = (
            Image.open(io.BytesIO(weapon))
            if weapon
            else cached_edit(
                cache,
                "weapon-" + hashlib.sha256(weapon_prompt.encode()).hexdigest()[:12],
                [white],
                weapon_prompt,
                editor,
                seed + 2,
            )
        )
        outputs["weapon"] = mask_background(weapon_image)
    result = {}
    for name, picture in outputs.items():
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        result[name] = buffer.getvalue()
    return result


@app.local_entrypoint()
def main(action: str = "setup", editor: str = "qwen-edit"):
    if action != "setup":
        raise ValueError("Use tools/batch.py for generation")
    print(setup.remote(editor))
