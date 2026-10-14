"""Stand-in for the skill's check_port.py / run_oss_gates.py / compare_layers.py:
`fake_port_check.py build|verify|probe <port dir>`."""
import json
import os
import sys
from pathlib import Path

phase, folder = sys.argv[1], Path(sys.argv[2])
if os.environ.get("FAKE_CHECK_ENV_LOG"):
    with open(os.environ["FAKE_CHECK_ENV_LOG"], "a") as f:
        f.write(json.dumps({"phase": phase, "env": sorted(os.environ)}) + "\n")
text = (folder / "arch.py").read_text()
if phase == "build" and "BROKEN" in text:
    print(f"Traceback (most recent call last):\nImportError: BROKEN layer in arch.py ({text.strip()})")
    sys.exit(1)
if phase == "verify" and os.environ.get("FAKE_CHECK_INFRA"):  # the Hub rate-limits the first N verify runs
    state = Path(os.environ["FAKE_CHECK_INFRA_STATE"])
    n = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(n))
    if n <= int(os.environ["FAKE_CHECK_INFRA"]):
        print("checkpoint_meta  FAIL  429 Too Many Requests for url: https://huggingface.co/acme/weird/resolve/main/x")
        sys.exit(1)
if phase == "verify" and "PARITY_BAD" in text:
    print("FAIL: prefill logprob differs from Hugging Face by 3.2 nats")
    sys.exit(1)
print(f"{phase}: ok")
