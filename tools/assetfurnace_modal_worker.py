#!/usr/bin/env python3
"""Run an installed AssetFurnace Studio to produce animated GLBs (inside Modal).

This worker runs with /af/home/image-to-3dlab/.venv/bin/python, not Modal's
Python. It deliberately uses AssetFurnace's public Studio client, avoiding
copy/pasting internal generation and rigging code.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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
    parser.add_argument("--run-name", required=True)
    return parser.parse_args()


def await_run(studio, run_id: str, run_status) -> dict:
    while True:
        status = run_status(studio, run_id=run_id, wait_seconds=20)
        label = status.get("current") or ""
        print(f"[AssetFurnace] {status.get('status')}: {label} "
              f"{status.get('percent')}% {status.get('message') or ''}", flush=True)
        if status["status"] == "done":
            return status
        if status["status"] in {"error", "failed", "cancelled"}:
            raise RuntimeError(f"Generation failed: {status.get('error') or status.get('log') or status}")
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


def main() -> None:
    args = parse()
    if not args.asset_id and not args.input:
        raise SystemExit("Provide --input or --asset-id.")
    if args.asset_id and args.input:
        raise SystemExit("Choose exactly one of --input or --asset-id.")
    moves = list(dict.fromkeys(s.strip() for s in args.moves.split(",") if s.strip()))
    if not moves:
        raise SystemExit("At least one animation move is required.")

    sys.path.insert(0, str(ASSETFURNACE))
    from image_to_3dlab.mcp_server import Studio, create, continue_asset, list_moves, run_status

    studio = Studio(base=STUDIO_URL, start=start_studio)
    available = list_moves(studio)
    if not available.get("installed"):
        raise RuntimeError("SkinTokens rig/animate not installed. Run Modal setup first.")
    all_moves = {x["id"] for x in available["moves"]}
    unknown = sorted(set(moves) - all_moves)
    if unknown:
        raise RuntimeError(f"Unknown animation moves {unknown}; available: {sorted(all_moves)}")

    if args.asset_id:
        identifier = args.asset_id
    else:
        src = args.input.resolve()
        if not src.is_file():
            raise FileNotFoundError(src)
        kind = src.suffix.lower()
        if kind not in {".png", ".jpg", ".jpeg", ".webp", ".glb"}:
            raise RuntimeError("Source must be PNG, JPG, WebP or GLB.")
        print(f"Creating {args.character} from {src.name} ...", flush=True)
        job = create(
            studio, want="character", description=args.character,
            image_path=str(src) if kind != ".glb" else None,
            model_path=str(src) if kind == ".glb" else None,
            front_and_back=False,
            engine=args.backend if kind != ".glb" else None,
            finish_upload=kind == ".glb",
            move=moves[0],
            finish_detail="game",
            advanced={"arms_away": 8, "texture_size": 2048},
        )
        completed = await_run(studio, job["run_id"], run_status)
        identifier = completed.get("asset_id")
        if not identifier:
            raise RuntimeError("AssetFurnace did not report an asset_id for this job.")
    for move in moves:
        record = asset_by_id(studio, identifier)
        if any(x.get("name") == move for x in record.get("clips", [])):
            print(f"Already generated: {move}", flush=True)
            continue
        print(f"Generating movement {move} ...", flush=True)
        job = continue_asset(studio, asset_id=identifier, up_to="animated", move=move)
        if "run_id" not in job:
            raise RuntimeError(f"No run ID for movement {move}: {job}")
        done = await_run(studio, job["run_id"], run_status)
        identifier = done.get("asset_id") or identifier

    record = asset_by_id(studio, identifier)
    clips = {c["name"]: c["file"] for c in record.get("clips", [])}
    missing = sorted(set(moves) - set(clips))
    if missing:
        raise RuntimeError(f"Missing animations: {missing}. Available: {sorted(clips)}")
    folder = EXPORTS / args.run_name
    folder.mkdir(parents=True, exist_ok=False)
    if record.get("rigged"):
        save_glb(record["rigged"], folder / "rigged.glb")
    for move in moves:
        save_glb(clips[move], folder / f"{move}.glb")

    meta = {"name": args.character, "asset_id": identifier,
            "moves": moves, "source": str(args.input) if args.input else None,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provenance": "See per-GLB .provenance.json files, if supplied"}
    (folder / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    archive = EXPORTS / (args.run_name + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as out:
        for file in sorted(folder.iterdir()):
            out.write(file, arcname=file.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(json.dumps({"result_zip": str(archive), "asset_id": identifier,
                      "sha256": digest, "bytes": archive.stat().st_size}))


if __name__ == "__main__":
    main()
