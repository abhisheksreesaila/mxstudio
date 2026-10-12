"""Stand-in for bwrap: logs its argv (FAKE_BWRAP_LOG, one JSON line) and runs the command after `--`
without any isolation, so tests can assert what the job asked bubblewrap to do."""
import json
import os
import sys

args = sys.argv[1:]
cut = args.index("--")
with open(os.environ["FAKE_BWRAP_LOG"], "a") as f:
    f.write(json.dumps({"bwrap": args[:cut], "cmd": args[cut + 1:],
                        "env": {k: v for k, v in os.environ.items() if k.startswith("HF_")}}) + "\n")
os.execvp(args[cut + 1], args[cut + 1:])
