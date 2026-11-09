#!/usr/bin/env python3
"""Run an installed AssetFurnace Studio to produce animated GLBs (inside Modal).

This worker runs with /af/home/image-to-3dlab/.venv/bin/python, not Modal's
Python. It deliberately uses AssetFurnace's public Studio client, avoiding
copy/pasting internal generation and rigging code.
"""

from __future__ import annotations

import argparse
import atexit
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from quality_workflow import check_review, parse_moves, safe_name, sha256, validate_options

WORKSPACE = Path("/af")
ASSETFURNACE = WORKSPACE / "home" / "image-to-3dlab"
EXPORTS = WORKSPACE / "exports"
STUDIO_URL = "http://127.0.0.1:8777"


def start_studio() -> None:
    # A cold Modal Volume can take longer than the Studio client's default wait.
    # Inherit stdout/stderr so a failed server startup is visible in Modal's logs.
    process = subprocess.Popen(
        [sys.executable, str(ASSETFURNACE / "viewer/serve.py"), "--no-browser"],
        cwd=ASSETFURNACE,
        stdin=subprocess.DEVNULL,
    )
    atexit.register(process.terminate)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"AssetFurnace Studio exited during startup ({process.returncode})")
        try:
            with urlopen(f"{STUDIO_URL}/api/catalog", timeout=15) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("AssetFurnace Studio did not answer within two minutes.")


def parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character", required=True)
    parser.add_argument("--input", type=Path, help="Source PNG/JPG/WebP or existing GLB")
    parser.add_argument("--asset-id", help="Reuse an existing AssetFurnace Library asset")
    parser.add_argument("--moves", default="idle,walk,run,sword_swing,hit_react,death")
    parser.add_argument("--backend", default="pixal3d")
    parser.add_argument("--back-input", type=Path)
    parser.add_argument("--options-json", required=True)
    parser.add_argument("--run-name", required=True)
    return parser.parse_args()


def await_run(studio, run_id: str, run_status) -> dict:
    while True:
        status = run_status(studio, run_id=run_id, wait_seconds=20)
        label = status.get("current") or ""
        print(
            f"[AssetFurnace] {status.get('status')}: {label} "
            f"{status.get('percent')}% {status.get('message') or ''}",
            flush=True,
        )
        if status["status"] == "done":
            return status
        if status["status"] in {"error", "failed", "cancelled"}:
            raise RuntimeError(
                f"Generation failed: {status.get('error') or status.get('log') or status}"
            )
        if status["status"] not in {"running", "queued", "pending"}:
            raise RuntimeError(f"Unknown generation status: {status}")


def asset_by_id(studio, identifier: str) -> dict:
    records = studio.get("api/assets").get("assets", [])
    result = next((a for a in records if a.get("id") == identifier), None)
    if result is None:
        raise RuntimeError(f"Asset ID not found in AssetFurnace Library: {identifier}")
    return result


def save_glb(relative: str, dest: Path) -> None:
    root = (ASSETFURNACE / "output").resolve()
    src = (root / relative).resolve()
    if not src.is_relative_to(root) or not src.is_file() or src.suffix.lower() != ".glb":
        raise RuntimeError(f"Unsafe/missing AssetFurnace GLB path: {relative}")
    with src.open("rb") as f:
        if f.read(4) != b"glTF":
            raise RuntimeError(f"Invalid GLB header: {src}")
    shutil.copy2(src, dest)
    evidence = src.with_suffix(".provenance.json")
    if evidence.is_file():
        shutil.copy2(evidence, dest.with_suffix(".provenance.json"))


def completed_id(studio, job, api) -> str:
    if "run_id" not in job:
        raise RuntimeError(f"No run ID returned: {job}")
    result = await_run(studio, job["run_id"], api.run_status)
    if not result.get("asset_id"):
        raise RuntimeError("AssetFurnace did not report an asset_id")
    return result["asset_id"]


def record_hash(record: dict, field: str) -> str:
    relative = record.get(field)
    if not relative:
        raise ValueError(f"Asset has no {field} file")
    root = (ASSETFURNACE / "output").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Asset file escapes the output directory")
    return sha256(path)


def surface_reference(record: dict) -> str:
    for field in ("finished", "model"):
        if record.get(field):
            return record[field]
    # Uploaded GLBs are not indexed as generated models. Their exact source is
    # kept beside the rig and bound to it through the input provenance hash.
    root = (ASSETFURNACE / "output").resolve()
    rig = (root / record["rigged"]).resolve()
    if not rig.is_relative_to(root):
        raise ValueError("Rig file escapes the output directory")
    source = (rig.parent / "input/source.glb").resolve()
    if not source.is_relative_to(root):
        raise ValueError("Rig source escapes the output directory")
    provenance = json.loads(rig.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    if provenance.get("input", {}).get("sha256") != sha256(source):
        raise ValueError("Rig source no longer matches its provenance")
    return source.relative_to(root).as_posix()


def reusable_rig(studio, source_hash: str) -> str | None:
    root = (ASSETFURNACE / "output").resolve()
    records = studio.get("api/assets").get("assets", [])
    for record in sorted(records, key=lambda value: value.get("updated", 0), reverse=True):
        if not record.get("rigged"):
            continue
        rig = (root / record["rigged"]).resolve()
        if not rig.is_relative_to(root):
            raise ValueError("Rig file escapes the output directory")
        sidecar = rig.with_suffix(".provenance.json")
        if not sidecar.is_file():
            continue
        provenance = json.loads(sidecar.read_text(encoding="utf-8"))
        if provenance.get("input", {}).get("sha256") == source_hash:
            surface = surface_reference(record)
            if record_hash({"surface": surface}, "surface") != source_hash:
                raise ValueError("Cached rig differs from the reviewed surface")
            return record["id"]
    return None


def run_pipeline(studio, api, args, options: dict, folder: Path) -> dict:
    stage = options["stage"]
    advanced = {"arms_away": options["arms_away"], "texture_size": options["texture_size"]}
    moves = parse_moves(options["moves"]) if stage in {"motion-test", "animate"} else []
    variants = {}
    identifier = args.asset_id
    if stage in {"model", "prop"}:
        if args.input:
            src = args.input.resolve()
            kind = src.suffix.lower()
            job = api.create(
                studio,
                want="prop" if stage == "prop" else "character",
                description=args.character,
                image_path=str(src) if kind != ".glb" else None,
                model_path=str(src) if kind == ".glb" else None,
                back_image_path=str(args.back_input) if args.back_input else None,
                front_and_back=options.get("generate_back", False),
                engine=args.backend if kind != ".glb" else None,
                finish_upload=kind == ".glb",
                stop_after_step="finished",
                finish_detail=options["detail"],
                advanced=advanced,
            )
        else:
            previous = asset_by_id(studio, identifier)
            job = api.continue_asset(
                studio,
                asset_id=identifier,
                up_to="finished",
                again=bool(previous.get("finished")),
                finish_detail=options["detail"],
                advanced=advanced,
            )
        identifier = completed_id(studio, job, api)
        selected = asset_by_id(studio, identifier)
        save_glb(selected["finished"], folder / "model.glb")
        if selected.get("model"):
            save_glb(selected["model"], folder / "raw_model.glb")
        variants[options["detail"]] = {"file": "model.glb", "asset_id": identifier}
        # Every comparison starts from the same source asset, never from a decimated variant.
        for detail in dict.fromkeys(options.get("compare_details", [])):
            if detail == options["detail"]:
                continue
            job = api.continue_asset(
                studio,
                asset_id=identifier,
                up_to="finished",
                again=True,
                finish_detail=detail,
                advanced=advanced,
            )
            variant_id = completed_id(studio, job, api)
            record = asset_by_id(studio, variant_id)
            filename = f"model_{detail}.glb"
            save_glb(record["finished"], folder / filename)
            variants[detail] = {"file": filename, "asset_id": variant_id}
    else:
        if args.input:
            if stage != "motion-test" or args.input.suffix.lower() != ".glb":
                raise ValueError("Only motion-test can import a reviewed finished GLB")
            review = options["reviews"].get("model", {})
            check_review(review, "model", {"model": sha256(args.input)})
            advanced["rig_from"] = "model"
            identifier = (
                reusable_rig(studio, sha256(args.input)) if options.get("reuse_rig") else None
            )
            if identifier:
                print(
                    f"[AssetFurnace] Resuming existing reviewed-source rig {identifier}", flush=True
                )
            else:
                job = api.create(
                    studio,
                    want="character",
                    description=args.character,
                    model_path=str(args.input.resolve()),
                    finish_upload=False,
                    stop_after_step="rigged",
                    advanced=advanced,
                )
                identifier = completed_id(studio, job, api)
            options["reviews"]["model"] = {**review, "asset_id": identifier}
        record = asset_by_id(studio, identifier)
        surface = surface_reference(record)
        if not record.get("finished"):
            advanced["rig_from"] = "model"
        hashes = {"model": record_hash({"surface": surface}, "surface")}
        check_review(options["reviews"].get("model", {}), "model", hashes, identifier)
        if stage == "animate":
            motion_review = options["reviews"].get("motion", {})
            tested = [key for key in motion_review.get("hashes", {}) if key != "model"]
            if len(tested) != 1:
                raise ValueError("A review of exactly one test clip is required")
            clip = next(
                (clip for clip in record.get("clips", []) if clip["name"] == tested[0]), None
            )
            if clip is None:
                raise ValueError("Reviewed test clip no longer exists")
            motion_hashes = {**hashes, tested[0]: record_hash({"clip": clip["file"]}, "clip")}
            check_review(motion_review, "motion", motion_hashes, identifier)
        available = api.list_moves(studio)
        if not available.get("installed"):
            raise ValueError("Install SkinTokens before starting animation")
        unknown = set(moves) - {move["id"] for move in available["moves"]}
        if unknown:
            raise ValueError(f"Unknown animations: {sorted(unknown)}")
        for move in moves:
            record = asset_by_id(studio, identifier)
            if any(clip["name"] == move for clip in record.get("clips", [])):
                continue
            job = api.continue_asset(
                studio,
                asset_id=identifier,
                up_to="animated",
                again=bool(record.get("rigged")),
                move=move,
                advanced=advanced,
            )
            identifier = completed_id(studio, job, api)
        record = asset_by_id(studio, identifier)
        if record_hash({"surface": surface_reference(record)}, "surface") != hashes["model"]:
            raise ValueError("Animation changed the reviewed model unexpectedly")
        save_glb(surface_reference(record), folder / "model.glb")
        save_glb(record["rigged"], folder / "rigged.glb")
        from rig_semantics import map_glb

        try:
            semantics = map_glb(folder / "rigged.glb", ASSETFURNACE / "scripts/kimodo_retarget.py")
        except (ValueError, KeyError, ImportError, OSError) as error:
            # Keep a valid rig usable with explicit bone selection if the mapper
            # cannot label this topology; never invent a hand by joint number.
            semantics = {"status": "manual_mapping_required", "reason": str(error)}
            print(f"[AssetFurnace] Hand mapping needs review: {error}", flush=True)
        (folder / "skeleton-map.json").write_text(json.dumps(semantics, indent=2), encoding="utf-8")
        carried_review = {**options["reviews"]["model"], "asset_id": identifier}
        (folder / "model-review.json").write_text(
            json.dumps(carried_review, indent=2) + "\n", encoding="utf-8"
        )
        clips = {clip["name"]: clip["file"] for clip in record.get("clips", [])}
        for move in moves:
            save_glb(clips[move], folder / f"{move}.glb")
    return {
        "name": args.character,
        "asset_id": identifier,
        "stage": stage,
        "moves": moves,
        "detail": options["detail"],
        "variants": variants,
        "review_required": "motion" if stage == "motion-test" else "model",
        "hashes": {path.name: sha256(path) for path in folder.glob("*.glb")},
        "source": str(args.input) if args.input else None,
        "reference_hashes": options["reviews"].get("reference", {}).get("hashes", {}),
        "views": "provided"
        if args.back_input
        else "generated"
        if options.get("generate_back")
        else "single",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> None:
    args = parse()
    if bool(args.asset_id) == bool(args.input):
        raise ValueError("Choose exactly one of --input or --asset-id")
    safe_name(args.run_name)
    options = json.loads(args.options_json)
    validate_options(
        options,
        has_image=bool(args.input and args.input.suffix.lower() != ".glb"),
        has_back=bool(args.back_input),
    )
    if (
        options["stage"] in {"motion-test", "animate"}
        and not args.asset_id
        and not (
            options["stage"] == "motion-test" and args.input and args.input.suffix.lower() == ".glb"
        )
    ):
        raise ValueError("Resume a reviewed asset ID for animation")
    if args.input and options["stage"] == "model" and args.input.suffix.lower() != ".glb":
        hashes = {"front": sha256(args.input)}
        if args.back_input:
            hashes["back"] = sha256(args.back_input)
        check_review(options["reviews"].get("reference", {}), "reference", hashes)
    sys.path.insert(0, str(ASSETFURNACE))
    from image_to_3dlab.mcp_server import Studio
    from image_to_3dlab import mcp_server as api

    studio = Studio(base=STUDIO_URL, start=start_studio)
    folder = EXPORTS / args.run_name
    folder.mkdir(parents=True, exist_ok=False)
    meta = run_pipeline(studio, api, args, options, folder)
    (folder / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    archive = folder.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as out:
        for file in sorted(folder.iterdir()):
            out.write(file, arcname=file.name)
    print(
        json.dumps(
            {
                "result_zip": str(archive),
                "asset_id": meta["asset_id"],
                "sha256": sha256(archive),
                "bytes": archive.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    main()
