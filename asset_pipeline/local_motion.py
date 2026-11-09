"""Local CPU motion recovery from an already-computed, provenance-bound rig."""

import json
from pathlib import Path

from .batch import digest


def validate_rig(rig: Path, model: Path) -> dict:
    if rig.read_bytes()[:4] != b"glTF":
        raise ValueError("Local rig must be a GLB")
    provenance = json.loads(rig.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    if provenance.get("input", {}).get("sha256") != digest(model):
        raise ValueError("Local rig was not built for the reviewed model")
    return provenance


def motion_sources(repository: Path) -> dict:
    files = [repository / "scripts" / name for name in ("kimodo_retarget.py", "arm_clearance.py")]
    motions = repository / "image_to_3dlab/motions"
    files.extend([motions / "presets.json", *sorted(motions.glob("*.npz"))])
    return {path.relative_to(repository).as_posix(): digest(path) for path in files}
