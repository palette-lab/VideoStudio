"""Runs ONE OpenMontage tool in its own process and prints its result as JSON.

Launched by execution.OpenMontageAdapter as `python -I openmontage_runner.py <repo> <module> <class> <inputs.json>`.
OpenMontage (AGPL-3.0) is used unmodified as a separate program: it is imported only inside this short-lived process,
never into the router itself. Only the tools listed in ALLOWED can be run.
"""

import importlib
import json
import sys

ALLOWED = {
    ("tools.video.video_stitch", "VideoStitch"),
    ("tools.audio.audio_mixer", "AudioMixer"),
}


def main() -> int:
    repo, module, cls, inputs_path = sys.argv[1:5]
    if (module, cls) not in ALLOWED:
        print(json.dumps({"success": False, "error": f"Tool {module}.{cls} is not allowed."}))
        return 2
    sys.path.insert(0, repo)
    with open(inputs_path) as f:
        inputs = json.load(f)
    try:
        tool = getattr(importlib.import_module(module), cls)()
        r = tool.execute(inputs)
        out = {"success": bool(r.success), "data": r.data, "artifacts": list(r.artifacts), "error": r.error}
    except Exception as e:  # report, never crash silently
        out = {"success": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
