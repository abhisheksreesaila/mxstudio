from huggingface_hub import snapshot_download
print("start", flush=True)
path = snapshot_download(
    "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8",
    ignore_patterns=["*.bin", "*.pth", "*.pt", "*.gguf", "*.onnx*", "original/*"],
)
print("done", path, flush=True)
