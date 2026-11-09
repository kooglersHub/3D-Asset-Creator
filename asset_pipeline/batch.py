"""Content-addressed serial batch runner with persisted stage results and review gates."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import traceback
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path

from .models import MODELS

STAGES = ("reference", "model", "refine", "finish", "motion-test", "animate", "package")
GATES = {
    "model": "reference",
    "motion-test": "finish",
    "animate": "motion-test",
    "package": "animate",
}
CLOUD_STAGES = {"reference", "model", "refine", "motion-test", "animate"}
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class Job:
    identifier: str
    source: Path
    config: dict

    def key(self) -> str:
        weapon = self.config.get("weapon", {}).get("image")
        return stable_hash(
            {
                "source": digest(self.source),
                "config": self.config,
                "weapon": digest(Path(weapon)) if weapon else None,
                "models": MODELS,
                "schema": 1,
            }
        )

    def stages(self):
        return STAGES if self.config["kind"] == "humanoid" else STAGES[:4] + ("package",)


def load_jobs(manifest: Path) -> list[Job]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("jobs"), list):
        raise ValueError("Expected a version 1 manifest with jobs")
    seen = set()
    result = []
    for raw in data["jobs"]:
        identifier = raw["id"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
            or identifier.casefold() in RESERVED
            or identifier.casefold() in seen
        ):
            raise ValueError(f"Invalid, reserved or duplicate job ID: {identifier}")
        seen.add(identifier.casefold())
        config = {
            "kind": "humanoid",
            "editor": "qwen-edit",
            "refine": False,
            "resolution": 512,
            "seed": 42,
            "triangles": 40000,
            "texture_size": 2048,
            "height": 1.9,
            "moves": ["idle", "walk", "run", "sword_swing", "hit_react", "death"],
            **data.get("defaults", {}),
            **raw,
        }
        if config["kind"] not in {"humanoid", "prop"}:
            raise ValueError("Automatic rigging supports humanoids; use prop for static creatures")
        if config["editor"] not in {"qwen-edit", "flux-klein"}:
            raise ValueError("Choose qwen-edit or flux-klein")
        if config["resolution"] not in {512, 1024} or type(config["refine"]) is not bool:
            raise ValueError("Resolution must be 512/1024 and refine must be boolean")
        if not 1000 <= config["triangles"] <= 100000 or config["texture_size"] not in {
            1024,
            2048,
            4096,
        }:
            raise ValueError("Invalid triangle or texture budget")
        if not 0.1 <= config["height"] <= 20:
            raise ValueError("Height must be between 0.1 and 20 meters")
        if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
            raise ValueError("Seed must be an integer from 0 to 4294967295")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", config.get("test_move", "idle")):
            raise ValueError("Invalid test move")
        weapon_config = config.get("weapon")
        if weapon_config is not None:
            if not isinstance(weapon_config, dict):
                raise ValueError("weapon must be an object")
            for field in ("position", "rotation_degrees"):
                vector = weapon_config.get(field, [0, 0, 0])
                if (
                    not isinstance(vector, list)
                    or len(vector) != 3
                    or any(
                        not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in vector
                    )
                ):
                    raise ValueError(f"weapon.{field} must contain three finite numbers")
            for field in ("scale", "length"):
                value = weapon_config.get(field, 1)
                if not isinstance(value, (int, float)) or not 0.001 <= value <= 20:
                    raise ValueError(f"weapon.{field} must be between 0.001 and 20")
        if not isinstance(config["moves"], list) or not config["moves"]:
            raise ValueError("Provide a nonempty moves list")
        for move in config["moves"]:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", move):
                raise ValueError(f"Invalid move: {move}")
        source = (manifest.parent / config.pop("image")).resolve()
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not source.is_file():
            raise ValueError(f"Missing/unsupported source image: {source}")
        if "weapon" in config and config["weapon"].get("image"):
            config["weapon"] = dict(config["weapon"])
            weapon = (manifest.parent / config["weapon"]["image"]).resolve()
            if not weapon.is_file():
                raise FileNotFoundError(weapon)
            config["weapon"]["image"] = str(weapon)
        result.append(Job(identifier, source, config))
    return result


class RunLock:
    """OS locks are released on crashes; never steal a live runner's batch."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise RuntimeError("Another runner is using this batch") from error
        return self

    def __exit__(self, *_):
        self.file.close()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "batch.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                job TEXT, stage TEXT, key TEXT, status TEXT, result TEXT, error TEXT,
                PRIMARY KEY(job,stage,key));
            CREATE TABLE IF NOT EXISTS reviews (
                job TEXT, stage TEXT, key TEXT, note TEXT, PRIMARY KEY(job,stage,key));
        """)

    def close(self):
        self.db.close()

    def task(self, job, stage):
        return self.db.execute(
            "SELECT * FROM tasks WHERE job=? AND stage=? AND key=?",
            (job.identifier, stage, job.key()),
        ).fetchone()

    def record(self, job, stage, status, result=None, error=None):
        self.db.execute(
            "INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)",
            (job.identifier, stage, job.key(), status, json.dumps(result), error),
        )
        self.db.commit()

    def reviewed(self, job, stage):
        task = self.task(job, stage)
        if task is None or task["status"] != "complete":
            return False
        result = json.loads(task["result"])
        review_key = stable_hash([job.key(), result.get("checksums")])
        return (
            intact(result)
            and self.db.execute(
                "SELECT 1 FROM reviews WHERE job=? AND stage=? AND key=?",
                (job.identifier, stage, review_key),
            ).fetchone()
            is not None
        )

    def approve(self, job, stage, note):
        task = self.task(job, stage)
        if task is None or task["status"] != "complete" or not intact(json.loads(task["result"])):
            raise ValueError("Only an intact completed stage can be reviewed")
        self.db.execute(
            "INSERT OR REPLACE INTO reviews VALUES (?,?,?,?)",
            (
                job.identifier,
                stage,
                stable_hash([job.key(), json.loads(task["result"])["checksums"]]),
                note,
            ),
        )
        self.db.commit()

    def reject(self, job, stage, note):
        task = self.task(job, stage)
        if task is None or task["status"] != "complete":
            raise ValueError("Only a completed stage can be rejected")
        stages = list(job.stages())
        for dependent in stages[stages.index(stage) :]:
            row = self.task(job, dependent)
            if row is not None:
                self.record(
                    job,
                    dependent,
                    "failed",
                    json.loads(row["result"]),
                    error=f"Review rejected {stage}: {note}",
                )


def intact(result: dict) -> bool:
    return bool(result and result.get("checksums")) and all(
        Path(path).is_file() and digest(Path(path)) == expected
        for path, expected in result["checksums"].items()
    )


class Runner:
    def __init__(self, store: Store, provider):
        self.store = store
        self.provider = provider

    def run(self, jobs, *, execute_cloud=False, max_cloud_tasks=1, retry_failed=False):
        if max_cloud_tasks < 0:
            raise ValueError("max-cloud-tasks cannot be negative")
        used = 0
        summary = []
        with RunLock(self.store.root / "runner.lock"):
            # A previous process died; no second runner can hold the lock now.
            self.store.db.execute(
                "UPDATE tasks SET status='failed', error='Interrupted; inspect artifacts before retry' WHERE status='running'"
            )
            self.store.db.commit()
            for stage in STAGES:
                with contextlib.ExitStack() as stack:
                    session = None
                    for job in jobs:
                        if stage not in job.stages():
                            continue
                        prior = list(job.stages())[: list(job.stages()).index(stage)]
                        tasks = {name: self.store.task(job, name) for name in prior}
                        if any(
                            row is None or row["status"] != "complete" for row in tasks.values()
                        ):
                            continue
                        inputs = {name: json.loads(row["result"]) for name, row in tasks.items()}
                        if any(not intact(value) for value in inputs.values()):
                            summary.append((job.identifier, stage, "corrupt-input"))
                            continue
                        chain = {}
                        stale = False
                        for name, value in inputs.items():
                            if value.get("input_digest") != stable_hash(chain):
                                stale = True
                                break
                            chain[name] = value["checksums"]
                        if stale:
                            summary.append((job.identifier, stage, "stale-input"))
                            continue
                        gate = GATES.get(stage)
                        if job.config["kind"] == "prop" and stage == "package":
                            gate = "finish"
                        if gate and not self.store.reviewed(job, gate):
                            summary.append((job.identifier, stage, "awaiting-review:" + gate))
                            continue
                        existing = self.store.task(job, stage)
                        input_digest = stable_hash(
                            {name: value["checksums"] for name, value in inputs.items()}
                        )
                        if existing and existing["status"] == "complete":
                            previous_result = json.loads(existing["result"])
                            if not intact(previous_result):
                                summary.append((job.identifier, stage, "corrupt-output"))
                            elif previous_result.get("input_digest") != input_digest:
                                summary.append((job.identifier, stage, "stale-output"))
                            continue
                        if existing and existing["status"] == "failed" and not retry_failed:
                            continue
                        cloud = stage in CLOUD_STAGES and not (
                            stage == "refine" and not job.config["refine"]
                        )
                        cloud = cloud and getattr(self.provider, "requires_cloud", lambda *_: True)(
                            stage, job
                        )
                        if cloud and (not execute_cloud or used >= max_cloud_tasks):
                            summary.append((job.identifier, stage, "cloud-budget-stop"))
                            continue
                        folder = self.store.root / job.identifier / job.key()[:16] / stage
                        if existing:
                            folder = folder / ("retry-" + uuid4().hex[:12])
                        folder.mkdir(parents=True, exist_ok=True)
                        self.store.record(job, stage, "running")
                        try:
                            if cloud:
                                used += 1
                            if session is None:
                                session = stack.enter_context(self.provider.session(stage))
                            result = self.provider.execute(stage, job, inputs, folder)
                            files = {
                                str(Path(path).resolve()): digest(Path(path))
                                for path in result["files"].values()
                            }
                            if not files:
                                raise ValueError("A successful task must produce files")
                            if any(
                                not Path(path).is_relative_to(folder.resolve()) for path in files
                            ):
                                raise ValueError("Task output escapes its artifact folder")
                            result["checksums"] = files
                            result["input_digest"] = input_digest
                            atomic_json(folder / "result.json", result)
                            self.store.record(job, stage, "complete", result)
                            summary.append((job.identifier, stage, "complete"))
                        except Exception as error:
                            atomic_json(
                                folder / "failure.json",
                                {
                                    "job": job.identifier,
                                    "stage": stage,
                                    "error": str(error),
                                    "traceback": traceback.format_exc(),
                                },
                            )
                            self.store.record(
                                job, stage, "failed", error=f"{type(error).__name__}: {error}"
                            )
                            summary.append((job.identifier, stage, "failed"))
        return summary
