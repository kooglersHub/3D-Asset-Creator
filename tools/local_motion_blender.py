"""Run AssetFurnace's pinned CPU retargeter inside a locally installed Blender."""

import argparse
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retarget", type=Path, required=True)
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    options, remaining = parser.parse_known_args(arguments)
    script = options.retarget.resolve()
    if not script.is_file():
        raise FileNotFoundError(script)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *remaining]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
