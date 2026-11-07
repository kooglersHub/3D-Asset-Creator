"""Shared validation and review records; no Modal or Blender dependencies."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

DETAILS = {"light", "game", "more", "high", "hero"}
STAGES = {"model", "motion-test", "animate", "prop"}
REFERENCE_CHECKS = (
    "one_character_full_body",
    "neutral_a_pose",
    "empty_hands",
    "arms_and_legs_separated",
    "even_lighting",
    "views_consistent",
)
MODEL_CHECKS = (
    "front_side_back_checked",
    "silhouette_and_proportions",
    "hands_and_armor",
    "no_holes_or_fused_equipment",
    "textures_and_lighting",
)
MOTION_CHECKS = (
    "whole_clip_watched",
    "feet_and_joints",
    "no_body_clipping",
    "rigid_equipment_stays_rigid",
    "cloth_acceptable",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def extract_export(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Preserve existing export: {destination}")
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Archive member escapes export directory")
        bundle.extractall(destination)


def safe_name(value: str) -> str:
    if not value or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value
    ):
        raise ValueError(f"Unsafe animation or run name: {value!r}")
    return value


def parse_moves(value: str) -> list[str]:
    return list(dict.fromkeys(safe_name(part.strip()) for part in value.split(",") if part.strip()))


def validate_options(options: dict, *, has_image=False, has_back=False) -> None:
    stage = options["stage"]
    if stage not in STAGES:
        raise ValueError(f"Stage must be one of {sorted(STAGES)}")
    if options["detail"] not in DETAILS:
        raise ValueError(f"Detail must be one of {sorted(DETAILS)}")
    if not 0 <= options["arms_away"] <= 25:
        raise ValueError("arms-away must be between 0 and 25 degrees")
    if options["texture_size"] not in {1024, 2048, 4096}:
        raise ValueError("texture-size must be 1024, 2048 or 4096")
    if has_back and (not has_image or options.get("generate_back")):
        raise ValueError("Use a back image with a front image, or generate-back, not both")
    if options.get("generate_back") and not has_image:
        raise ValueError("generate-back requires a new front image")
    if stage in {"motion-test", "animate"} and has_image:
        raise ValueError("First generate and review a static model; resume its asset ID")
    if stage in {"motion-test", "animate"} and options.get("compare_details"):
        raise ValueError("Detail comparisons belong to the model stage")
    for detail in options.get("compare_details", []):
        if detail not in DETAILS:
            raise ValueError(f"Unsupported comparison detail: {detail}")
    moves = parse_moves(options.get("moves", ""))
    if stage == "motion-test" and len(moves) != 1:
        raise ValueError("motion-test requires exactly one animation")
    if stage == "animate" and not moves:
        raise ValueError("animate requires at least one animation")


def check_review(review: dict, kind: str, hashes: dict, asset_id: str | None = None) -> None:
    checks = {"reference": REFERENCE_CHECKS, "model": MODEL_CHECKS, "motion": MOTION_CHECKS}[kind]
    if review.get("kind") != kind or review.get("accepted") is not True:
        raise ValueError(f"An accepted {kind} review is required")
    if any(review.get("checks", {}).get(check) is not True for check in checks):
        raise ValueError(f"Complete all {kind} review checks")
    if asset_id is not None and review.get("asset_id") != asset_id:
        raise ValueError(f"The {kind} review belongs to a different asset")
    if review.get("hashes") != hashes:
        raise ValueError(f"The {kind} review is stale: files changed")


def review_template(kind: str, hashes: dict, asset_id: str | None = None) -> dict:
    checks = {"reference": REFERENCE_CHECKS, "model": MODEL_CHECKS, "motion": MOTION_CHECKS}[kind]
    return {
        "kind": kind,
        "asset_id": asset_id,
        "hashes": hashes,
        "accepted": False,
        "checks": dict.fromkeys(checks, False),
        "notes": "",
    }
