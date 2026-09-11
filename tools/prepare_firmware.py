#!/usr/bin/env python3
"""Copy (and optionally build) the ESP-IDF wifi example used by the WiFi HW test harness.

  python tools/prepare_firmware.py --target esp32s3 --dest firmware/iperf-esp32s3 \
      --idf-path D:/IDF_5_5_AI/.espressif/v5.5.4/esp-idf \
      --tools-path C:/Espressif \
      --python-env C:/Espressif/tools/python/v5.5.4/venv \
      --path-prepend C:/Espressif/tools/xtensa-esp-elf/esp-14.2.0_20260121/xtensa-esp-elf/bin \
      --path-prepend C:/Espressif/tools/cmake/3.30.2/bin \
      --path-prepend C:/Espressif/tools/ninja/1.12.1 \
      --build

Without --build it only copies the example and prints the commands to run.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

EXAMPLES = {"iperf": "wifi/iperf", "scan": "wifi/scan"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--example", default="iperf", choices=sorted(EXAMPLES), help="which IDF example")
    p.add_argument("--target", required=True, help="esp32s3, esp32c6, esp32, ...")
    p.add_argument("--dest", required=True, help="destination project directory")
    p.add_argument("--idf-path", required=True, help="path to the esp-idf checkout")
    p.add_argument("--tools-path", default=None, help="IDF_TOOLS_PATH (must not contain spaces)")
    p.add_argument("--python-env", default=None, help="IDF_PYTHON_ENV_PATH (the IDF venv)")
    p.add_argument("--python", default=None, help="python executable (default: --python-env python)")
    p.add_argument("--path-prepend", action="append", default=[], help="tool bin dir to prepend to PATH")
    p.add_argument("--build", action="store_true", help="run idf.py set-target + build")
    args = p.parse_args()

    src = os.path.join(args.idf_path, "examples", EXAMPLES[args.example])
    if not os.path.isdir(src):
        print("ERROR: example not found: %s" % src, file=sys.stderr)
        return 2

    dest = os.path.abspath(args.dest)
    if os.path.isdir(dest) and os.listdir(dest):
        print("! %s already exists and is not empty - not overwriting" % dest)
    else:
        shutil.copytree(src, dest)
        print("copied %s -> %s" % (src, dest))

    python = args.python or (os.path.join(args.python_env, "Scripts", "python.exe")
                             if args.python_env else sys.executable)
    idf_py = os.path.join(args.idf_path, "tools", "idf.py")

    env = os.environ.copy()
    env["IDF_PATH"] = args.idf_path
    env["IDF_PYTHON_CHECK_CONSTRAINTS"] = "no"
    if args.tools_path:
        env["IDF_TOOLS_PATH"] = args.tools_path
    if args.python_env:
        env["IDF_PYTHON_ENV_PATH"] = args.python_env
    for d in args.path_prepend:
        env["PATH"] = d + os.pathsep + env["PATH"]

    print("\nTo build and flash manually:")
    print("  set IDF_PATH=%s" % args.idf_path)
    print("  %s %s set-target %s   (in %s)" % (python, idf_py, args.target, dest))
    print("  %s %s build" % (python, idf_py))
    print("  %s %s -p <PORT> flash" % (python, idf_py))

    if not args.build:
        return 0

    for cmd in (["set-target", args.target], ["build"]):
        full = [python, idf_py] + cmd
        print("\n>>> %s" % " ".join(full), flush=True)
        rc = subprocess.run(full, cwd=dest, env=env).returncode
        if rc != 0:
            print("ERROR: idf.py %s failed (%d)" % (" ".join(cmd), rc), file=sys.stderr)
            return rc
    print("\nOK: firmware built in %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
