"""Stand-in for the skill's check_port.py / run_oss_gates.py: `fake_port_check.py build|verify <port dir>`."""
import sys
from pathlib import Path

phase, folder = sys.argv[1], Path(sys.argv[2])
text = (folder / "arch.py").read_text()
if phase == "build" and "BROKEN" in text:
    print("Traceback (most recent call last):\nImportError: BROKEN layer in arch.py")
    sys.exit(1)
if phase == "verify" and "PARITY_BAD" in text:
    print("FAIL: prefill logprob differs from Hugging Face by 3.2 nats")
    sys.exit(1)
print(f"{phase}: ok")
