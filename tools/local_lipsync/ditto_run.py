"""Run Ditto (antgroup/ditto-talkinghead, Apache-2.0) on this machine: a photo + speech -> a lip-synced talking video.

Ditto's PyTorch release assumes an NVIDIA GPU ("cuda" in its config). This runner rewrites every device in the config
to the one asked for (Apple GPU "mps", or "cpu"), so it runs on a Mac or any CPU server. Its audio model (HuBERT, ONNX)
always runs on the CPU through onnxruntime.

Usage (with Ditto's own venv):
  python ditto_run.py --repo <ditto checkout> --image face.png --audio speech.wav --out talk.mp4 [--device mps|cpu]
Prints one JSON line at the end: {"ok": true, "path": ..., "seconds": ..., "device": ...}.
"""

import argparse
import json
import os
import pickle
import sys
import tempfile
import time


def patch_devices(obj, device):
    """Every "device" value in Ditto's nested config -> `device`."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "device" and isinstance(v, str):
                obj[k] = device
            else:
                patch_devices(v, device)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            patch_devices(v, device)
    return obj


def pick_device(want: str) -> str:
    import torch
    if want == "auto":
        return "mps" if torch.backends.mps.is_available() else "cpu"
    if want == "mps" and not torch.backends.mps.is_available():
        return "cpu"
    return want


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu"])
    a = ap.parse_args()
    a.image, a.audio, a.out = (os.path.abspath(p) for p in (a.image, a.audio, a.out))  # before chdir

    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # ops MPS lacks (e.g. 3-D grid_sample) fall back to CPU
    repo = os.path.abspath(a.repo)
    sys.path.insert(0, repo)
    os.chdir(repo)  # Ditto resolves its Cython blend module and tmp files relative to the checkout
    device = pick_device(a.device)

    with open(os.path.join(repo, "checkpoints/ditto_cfg/v0.4_hubert_cfg_pytorch.pkl"), "rb") as f:
        cfg = patch_devices(pickle.load(f), device)
    tmp = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)
    pickle.dump(cfg, tmp)
    tmp.close()

    import importlib
    import inspect

    from core.utils import load_model as lm

    def create_model(model_path, device="cuda", module_name="", package_name="..models.modules", **kw):
        """Ditto's create_model, except that networks taking a device (e.g. LMDM) get it; Ditto leaves them on their
        "cuda" default."""
        mod = getattr(importlib.import_module(package_name, "core.utils"), module_name)
        if "device" in inspect.signature(mod.__init__).parameters:
            kw["device"] = device
        model = mod(**kw)
        model.load_model(model_path).to(device)
        return model

    lm.create_model = create_model

    import inference  # Ditto's own entry: run(SDK, audio, source, output)
    from stream_pipeline_offline import StreamSDK

    t0 = time.time()
    sdk = StreamSDK(tmp.name, os.path.join(repo, "checkpoints/ditto_pytorch"))
    inference.run(sdk, os.path.abspath(a.audio), os.path.abspath(a.image), os.path.abspath(a.out))
    ok = os.path.exists(a.out) and os.path.getsize(a.out) > 0
    print(json.dumps({"ok": ok, "path": os.path.abspath(a.out), "seconds": round(time.time() - t0, 1), "device": device}))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
