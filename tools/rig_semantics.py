"""Reuse AssetFurnace's humanoid mapper to label numbered SkinTokens joints."""

import importlib.util
import json
import struct
from pathlib import Path


def map_glb(path: Path, mapper_path: Path) -> dict:
    import numpy as np

    raw = path.read_bytes()
    if raw[:4] != b"glTF" or raw[16:20] != b"JSON":
        raise ValueError("Expected a GLB JSON chunk")
    length = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20 : 20 + length])
    if len(document.get("skins", [])) != 1:
        raise ValueError("Semantic mapping expects one skin")
    nodes = document["nodes"]
    joints = document["skins"][0]["joints"]
    parents = {
        child: index for index, node in enumerate(nodes) for child in node.get("children", [])
    }
    cache = {}

    def world(index):
        if index in cache:
            return cache[index]
        node = nodes[index]
        if "matrix" in node:
            matrix = np.array(node["matrix"]).reshape(4, 4).T
        else:
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            matrix = np.eye(4)
            matrix[:3, :3] = np.array(
                [
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ]
            ) @ np.diag(node.get("scale", [1, 1, 1]))
            matrix[:3, 3] = node.get("translation", [0, 0, 0])
        cache[index] = world(parents[index]) @ matrix if index in parents else matrix
        return cache[index]

    bone_parents = {}
    heads = {}
    for index in joints:
        name = nodes[index]["name"]
        parent = parents.get(index)
        while parent is not None and parent not in joints:
            parent = parents.get(parent)
        bone_parents[name] = nodes[parent]["name"] if parent is not None else None
        heads[name] = world(index)[:3, 3]
    specification = importlib.util.spec_from_file_location("assetfurnace_semantics", mapper_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    mapping, _ = module.map_skintokens_to_soma(bone_parents, heads, up=np.array([0.0, 1.0, 0.0]))
    candidates = [name for name, semantic in mapping.items() if semantic == "RightHand"]
    if len(candidates) != 1:
        raise ValueError(f"Ambiguous right hand: {candidates}")
    return {
        "status": "mapped",
        "hand_bone": candidates[0],
        "bones": mapping,
        "method": "AssetFurnace humanoid topology and rest geometry; no fixed joint indices",
    }
