"""Create review templates tied to the exact reference or exported GLB files."""

import argparse
from pathlib import Path

from quality_workflow import read_json, review_template, sha256, write_json

REFERENCE_BRIEF = """# Character reference brief

Preserve the character's design, proportions, helmet and armor.
Create a full-body orthographic front view in a relaxed A-pose.
Arms angle down about 45 degrees, hands open and empty, legs separated.
Use soft even lighting, neutral materials and a plain background.
Show the entire silhouette with margins. Keep both arms clear of the torso.
Create a matching rear view with the same pose, height, scale and equipment.
Generate the sword, shield and other handheld props as separate images.
Avoid dramatic colored illumination and deep shadows in the reference.

This brief is for preparing new artwork. This tool does not infer a correct
A-pose, remove baked lighting or guarantee front/back consistency from pixels.
Review both pictures before marking reference-review.json accepted.
"""


def prepare(front: Path, back: Path | None, destination: Path) -> None:
    from PIL import Image

    hashes = {"front": sha256(front)}
    facts = {}
    for name, path in (("front", front), ("back", back)):
        if path is None:
            continue
        hashes[name] = sha256(path)
        with Image.open(path) as picture:
            facts[name] = {"pixels": list(picture.size), "mode": picture.mode}
    write_json(destination / "reference-review.json", review_template("reference", hashes))
    write_json(destination / "reference-info.json", facts)
    (destination / "reference-brief.md").write_text(REFERENCE_BRIEF, encoding="utf-8")


def export_review(directory: Path, kind: str) -> dict:
    manifest = read_json(directory / "manifest.json")
    hashes = {"model": sha256(directory / "model.glb")}
    if kind == "motion":
        moves = manifest["moves"]
        if len(moves) != 1:
            raise ValueError("Review a single motion-test clip before expanding animations")
        hashes[moves[0]] = sha256(directory / f"{moves[0]}.glb")
    return review_template(kind, hashes, manifest["asset_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--front", type=Path, required=True)
    prepare_parser.add_argument("--back", type=Path)
    prepare_parser.add_argument("--output", type=Path, required=True)
    review_parser = sub.add_parser("template")
    review_parser.add_argument("--kind", choices=["model", "motion"], required=True)
    review_parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.front, args.back, args.output)
    else:
        write_json(args.export / f"{args.kind}-review.json", export_review(args.export, args.kind))


if __name__ == "__main__":
    main()
