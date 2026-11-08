"""Hi3D metadata and local input validation, independent of GPU dependencies."""

from pathlib import Path

from quality_workflow import read_json, sha256

CODE_REVISION = "13657ad09afaa92e350b461a1cd66399e44a59c4"
MODEL_REVISIONS = {
    "hi3d-official/Hi3Dv1.0": "d502e938cbedc6e87aba8884614c5ca6d177a4b6",
    "facebook/dinov2-large": "47b73eefe95e8d44ec3623f8890bd894b6ea2d6c",
    "openai/clip-vit-large-patch14": "32bd64288804d66eefd0ccbe215aa642df71cc41",
}
ATTRIBUTION = "Powered by Hi3D 1.0"
LICENSE_URL = "https://github.com/hi3d-official/Hi3Dv1.0/blob/" + CODE_REVISION + "/LICENSE"


def validate_settings(resolution: int, steps: int) -> None:
    if resolution not in {512, 1024, 1536}:
        raise ValueError("Hi3D resolution must be 512, 1024 or 1536")
    if not 1 <= steps <= 100:
        raise ValueError("Inference steps must be between 1 and 100")


def resolve_coarse(model_path: str, coarse_export: str, reference: Path) -> Path:
    if bool(model_path) == bool(coarse_export):
        raise ValueError("Choose exactly one of --model-path or --coarse-export")
    if coarse_export:
        directory = Path(coarse_export).expanduser().resolve()
        manifest = read_json(directory / "manifest.json")
        if manifest.get("moves"):
            raise ValueError("Use a static export, before rigging and animation")
        expected = manifest.get("reference_hashes", {}).get("front")
        if expected and expected != sha256(reference):
            raise ValueError("Hi3D requires the same reference image as the coarse generation")
        source = directory / "raw_model.glb"
        if not source.is_file():
            source = directory / "model.glb"
    else:
        source = Path(model_path).expanduser().resolve()
    if source.suffix.lower() != ".glb" or not source.is_file():
        raise ValueError("Provide a static coarse GLB")
    with source.open("rb") as mesh:
        if mesh.read(4) != b"glTF":
            raise ValueError("Invalid coarse GLB header")
    return source


def geometry_manifest(
    image_hash: str, coarse_hash: str, resolution: int, steps: int, seed: int, offload: bool
) -> dict:
    return {
        "stage": "geometry-review",
        "generator": "Hi3D 1.0 refinement",
        "attribution": ATTRIBUTION,
        "license": LICENSE_URL,
        "code_revision": CODE_REVISION,
        "model_revisions": MODEL_REVISIONS,
        "reference_hashes": {"front": image_hash},
        "coarse_sha256": coarse_hash,
        "resolution": resolution,
        "steps": steps,
        "seed": seed,
        "sequential_cpu_offload": offload,
        "moves": [],
        "variants": {},
        "geometry_only": True,
        "textures_preserved": False,
        "coordinate_space": "Hi3D normalized space; recentered, unit maximum radius",
        "review_required": "geometry, texture transfer and a new model review before rigging",
    }
