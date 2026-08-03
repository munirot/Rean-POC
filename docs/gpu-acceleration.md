# GPU / hardware acceleration for the face-service

Face detection + embedding (InsightFace, via onnxruntime) is the heaviest part of
recognition. It runs on **CPU by default**. This is how to accelerate it — on an
Apple-Silicon dev Mac (CoreML) and on a Linux/NVIDIA production box (CUDA).

The device is chosen with the `DEVICE` env var (in `face-service/.env`). The
service resolves it to onnxruntime execution providers in
[`app/config.py`](../face-service/app/config.py) (`providers` / `ctx_id`). CPU is
always kept as the last provider, so if an accelerated provider isn't available in
your onnxruntime build, inference **silently falls back to CPU** rather than failing.

| `DEVICE`            | Providers                                   | Where it applies                 |
|--------------------|---------------------------------------------|----------------------------------|
| `cpu` (default)    | CPU                                          | anywhere                         |
| `coreml` / `mps`   | CoreML → CPU                                 | Apple Silicon (M-series) macOS   |
| `gpu` / `cuda`     | CUDA → CPU                                   | Linux/Windows with NVIDIA GPU    |

After changing `DEVICE`, **restart the service** (uvicorn isn't run with `--reload`).
Confirm what actually loaded via the startup log line
`InsightFace model '…' ready (device=…, …)` and the `/api/health` `device` field.

---

## Apple Silicon (dev Mac) — `DEVICE=coreml`

The standard `onnxruntime` wheel for macOS arm64 already ships the CoreML execution
provider (verified here: onnxruntime 1.27.0, arm64, `CoreMLExecutionProvider`
present) — **no extra install needed**.

```bash
# face-service/.env
DEVICE=coreml
```

Then restart the service.

Caveats (this is why it's "best-effort", not a guaranteed speedup):
- InsightFace's CoreML support is imperfect: some ops aren't CoreML-compatible and
  transparently run on CPU, so the win varies by model and is usually smaller than a
  discrete GPU.
- The **first** inference after start is slow — CoreML compiles the model for the
  Neural Engine once. Warm inferences are what to measure.
- If it isn't faster for your workload, set `DEVICE=cpu` — the vectorized matching
  and the frontend/backend split already keep the interactive path responsive.

Check which provider a session really used:

```bash
cd face-service
.venv/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

---

## Production: Linux + NVIDIA GPU — `DEVICE=gpu`

Biggest win, and the existing `DEVICE=gpu` switch already targets it.

1. **Host requirements:** NVIDIA GPU, matching NVIDIA driver, and a CUDA/cuDNN
   runtime compatible with your onnxruntime version (see the onnxruntime CUDA
   compatibility matrix).
2. **Install the GPU runtime** — the CPU `onnxruntime` wheel has no CUDA provider.
   Swap it for `onnxruntime-gpu`:
   ```bash
   pip uninstall -y onnxruntime
   pip install onnxruntime-gpu
   ```
   (Or edit `face-service/requirements.txt` to pin `onnxruntime-gpu` for the
   production image; keep `onnxruntime` for CPU/Mac dev.)
3. **Configure + restart:**
   ```bash
   # face-service/.env
   DEVICE=gpu
   ```
4. **Verify** the CUDA provider is present and used:
   ```bash
   python -c "import onnxruntime as ort; print(ort.get_available_providers())"
   # expect 'CUDAExecutionProvider' in the list
   ```
   and check the `/api/health` `device` field is `gpu`.

Containers: use an `nvidia/cuda` base image and run with the NVIDIA container
runtime (`--gpus all`).

---

## Related tuning (already in place)

- **Vectorized matching** — identity matching is a single NumPy matrix multiply
  (`best_match_vec` in [`app/engine.py`](../face-service/app/engine.py)) over a
  cached gallery matrix, so it stays cheap as enrollment grows and doesn't need a
  GPU. The gallery is cached in memory and rebuilt only when enrollment changes.
- **Lighter model** (not enabled) — `MODEL_PACK=buffalo_s` swaps ArcFace r100 for a
  smaller/faster model. It's a CPU-side speedup with a small accuracy trade-off;
  flip it via env if CPU latency matters more than top-end accuracy. Enrollment
  quality is best kept on `buffalo_l`.
- **Frontend detection** — the live bounding box is detected in-browser (MediaPipe),
  so the backend model only runs for identity a few times/sec, not every frame.
