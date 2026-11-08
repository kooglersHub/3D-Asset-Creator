"""Resumable image-to-character batches. Planning/status/review never start cloud work."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asset_pipeline.batch import STAGES, Runner, Store, atomic_json, load_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "plan", "run", "status", "approve", "reject"])
    parser.add_argument("--manifest", type=Path, default=Path("batch.json"))
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("exports/batch"))
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path("C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"),
    )
    parser.add_argument("--execute-cloud", action="store_true")
    parser.add_argument(
        "--max-cloud-tasks",
        type=int,
        default=1,
        help="Limit stage-jobs, not dollars; one stage can produce body and weapon",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--job", action="append", help="Select IDs; repeat for several")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if args.action == "init":
        if args.manifest.exists():
            raise FileExistsError(args.manifest)
        if not args.input_dir or not args.input_dir.is_dir():
            parser.error("init requires --input-dir")
        paths = sorted(
            path
            for path in args.input_dir.rglob("*")
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        if not paths:
            parser.error("No images found")
        import re

        jobs = [
            {
                "id": f"{index:03d}_" + re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)[:55],
                "image": str(path.resolve()),
            }
            for index, path in enumerate(paths, 1)
        ]
        atomic_json(args.manifest, {"version": 1, "defaults": {"refine": False}, "jobs": jobs})
        print(f"Created {args.manifest} with {len(jobs)} jobs; check creature kinds before running")
        return
    jobs = load_jobs(args.manifest.resolve())
    if args.job:
        unknown = set(args.job) - {job.identifier for job in jobs}
        if unknown:
            parser.error(f"Unknown jobs: {sorted(unknown)}")
        jobs = [job for job in jobs if job.identifier in args.job]
    if args.action == "plan":
        print(
            json.dumps(
                [
                    {
                        "id": job.identifier,
                        "source": str(job.source),
                        "stages": job.stages(),
                        "key": job.key(),
                        "config": job.config,
                    }
                    for job in jobs
                ],
                indent=2,
            )
        )
        return
    store = Store(args.output)
    try:
        if args.action in {"approve", "reject"}:
            if not args.job or not args.stage or not args.note.strip():
                parser.error("review requires --job, --stage and --note after visual inspection")
            from asset_pipeline.batch import RunLock

            with RunLock(store.root / "runner.lock"):
                for job in jobs:
                    getattr(store, args.action)(job, args.stage, args.note)
                    print(f"{args.action}: {job.identifier}/{args.stage}")
        elif args.action == "status":
            for job in jobs:
                for stage in job.stages():
                    task = store.task(job, stage)
                    status = task["status"] if task else "pending"
                    reviewed = " reviewed" if store.reviewed(job, stage) else ""
                    print(f"{job.identifier:24} {stage:12} {status}{reviewed}")
                    if task and task["error"]:
                        print("  " + task["error"])
                    if task and task["status"] == "complete":
                        print("  " + next(iter(json.loads(task["result"])["checksums"])))
        else:
            from asset_pipeline.providers import Providers

            summary = Runner(store, Providers(args.blender)).run(
                jobs,
                execute_cloud=args.execute_cloud,
                max_cloud_tasks=args.max_cloud_tasks,
                retry_failed=args.retry_failed,
            )
            for row in summary:
                print(" / ".join(row))
            if any(
                row[2]
                in {"failed", "corrupt-output", "corrupt-input", "stale-output", "stale-input"}
                for row in summary
            ):
                raise SystemExit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
