def step(sid, app, caps, inputs, outputs=("video_file",), subtasks=("t1",)):
    return {"step_id": sid, "app_id": app, "capabilities_used": list(caps), "subtask_ids": list(subtasks),
            "inputs": [{"source": s, "format": f} for s, f in inputs], "outputs": list(outputs), "responsibility": "x"}


def und(*subtasks):
    return {"subtasks": [{"id": f"t{i + 1}", "description": d, "required_capabilities": caps} for i, (d, caps) in enumerate(subtasks)]}


IMG_PRODUCT = {"a1": {"modality": "image", "tags": ["product"], "filename": "shoe.jpg"}}
IMG_GARMENT = {"a1": {"modality": "image", "tags": ["garment", "person"], "filename": "dress.jpg"}}
VIDEO = {"a1": {"modality": "video", "tags": ["person"], "filename": "clip.mp4"}}
AUDIO = {"a1": {"modality": "audio", "tags": [], "filename": "track.mp3"}}
