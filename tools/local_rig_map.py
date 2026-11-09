"""Map rig joints using Blender's bundled numpy without installing local ML packages."""

import argparse
import json
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rig_semantics import map_glb

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig", type=Path, required=True)
    parser.add_argument("--mapper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.output.write_text(json.dumps(map_glb(args.rig, args.mapper), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
