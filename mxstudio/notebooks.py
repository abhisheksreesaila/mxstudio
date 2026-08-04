"""Generate and run per-model notebooks inside mxstudio (no Jupyter install needed)."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from mxstudio.registry import ModelEntry
from mxstudio.servers import MODEL_PORT, PROJECT_ROOT

NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"

# Tiny REPL runner: one process per notebook, state preserved across cells.
_KERNEL_BOOTSTRAP = r"""
import json, sys, traceback
from io import StringIO

ns = {"__name__": "__main__"}
while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        req = json.loads(line)
    except Exception as e:
        print(json.dumps({"ok": False, "stdout": "", "stderr": f"bad request: {e}"}), flush=True)
        continue
    code = req.get("code", "")
    out, err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    ok = True
    try:
        compiled = compile(code, "<cell>", "exec")
        exec(compiled, ns, ns)
    except Exception:
        ok = False
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    print(json.dumps({"ok": ok, "stdout": out.getvalue(), "stderr": err.getvalue()}), flush=True)
"""


def _md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- _(none listed)_"


def build_notebook(entry: ModelEntry) -> dict:
    """Build an educational lab notebook: teach first, then run code."""
    from mxstudio.modelinfo import summarize_model_card

    links = "\n".join(f"- [{label}]({url})" for label, url in entry.links.items())
    goals = _bullets(entry.learning_goals)
    ideas = _bullets(entry.key_ideas)
    family = entry.family or "Open language model"

    # Pull a compact excerpt from the Hugging Face model card when available.
    hf_excerpt = summarize_model_card(entry.id)
    if hf_excerpt:
        hf_section = f"""## From the Hugging Face model card

The block below is auto-excerpted from the upstream README (noisy tables stripped).
Use it as primary-source reading — then cross-check against the paper.

{hf_excerpt}
"""
    else:
        hf_section = """## From the Hugging Face model card

_(Model card not available offline yet. Download the model once, re-generate this
notebook, or open the Hugging Face link above.)_
"""

    title = f"""# Lab: {entry.name}

**Educational notebook** · family: *{family}* · served via Modular MAX

| | |
|---|---|
| **Model id** | `{entry.id}` |
| **Size** | {entry.params} (~{entry.approx_gb} GB on disk) |
| **Context** | {entry.context:,} tokens |
| **License** | {entry.license} |
| **Status** | {"Experimental in mxstudio" if entry.experimental else "Supported"} |

{entry.description}

### Learning goals

{goals}

### How to use this notebook

1. In the **Models** tab, **Serve** `{entry.id}` and wait until the pill says **running**.
2. Read the background sections below (they are the lesson).
3. Run the lab cells with **Shift+Enter** or **Run all**.
4. Optional: edit prompts and re-run — the notebook is meant to be played with.
"""

    what = f"""## What is this model?

{entry.description}

**Family:** {family}

Think of this checkpoint as one point on a design trade-off curve: parameter count,
context length, openness, and serving cost. The rest of this notebook is about
understanding *why* this point on the curve is interesting — not only how to call the API.
"""

    innovation = f"""## What's the innovation?

{entry.innovation}

### Key ideas to take away

{ideas}
"""

    architecture = f"""## Architecture & training (plain language)

{entry.architecture or "_Architecture notes coming soon for this model._"}

### When should you pick this model?

{entry.when_to_use or "_See the description above._"}
"""

    resources = f"""## Papers, code, and cards

Read at least the abstract of the paper before you finish the lab.

{links}

> Tip: after you finish the coding cells, come back and skim one section of the
> paper (training data, post-training, or evaluation). That loop — **run → read →
> re-prompt** — is the whole point of an educational notebook.
"""

    hf_md = hf_section

    lab_intro = f"""## Lab: talk to the model

The cells below hit the local OpenAI-compatible server that mxstudio starts
(`http://localhost:{MODEL_PORT}/v1`). Nothing leaves your machine.

Make sure **{entry.name}** is **running** before you continue.
"""

    setup_md = """### 1. Connect

Create an OpenAI client pointed at the local MAX server and list the loaded model.
"""

    setup = f'''from openai import OpenAI

MODEL = "{entry.id}"
BASE = "http://localhost:{MODEL_PORT}/v1"
client = OpenAI(base_url=BASE, api_key="EMPTY")

models = [m.id for m in client.models.list()]
print("endpoint:", BASE)
print("available:", models)
assert any(MODEL in m or m in MODEL for m in models) or models, "Server returned no models — is Serve running?"'''

    teach_md = """### 2. Ask the model to teach you

First completion: have the model explain itself. Small models can be wrong —
treat the answer as a *starting point*, then compare with the innovation section above.
"""

    teach = '''response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {
            "role": "user",
            "content": (
                "You are helping a student. In 4 short bullet points, explain: "
                "(1) what kind of model you are, (2) roughly how large you are, "
                "(3) one thing you are good at, (4) one limitation they should remember. "
                "Be honest; do not invent papers."
            ),
        }
    ],
    max_tokens=220,
    temperature=0.4,
)
print(response.choices[0].message.content)'''

    stream_md = """### 3. Streaming tokens

Streaming returns tokens as they are generated. That is what makes chat UIs feel live —
and it is how we will measure time-to-first-token later.
"""

    streaming = '''stream = client.chat.completions.create(
    model=MODEL,
    messages=[{
        "role": "user",
        "content": "In one short paragraph, what is 'post-training' for LLMs, and why does it matter for small models?",
    }],
    max_tokens=160,
    temperature=0.5,
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()'''

    temp_md = """### 4. Temperature playground

**Temperature** controls randomness. `0.0` ≈ deterministic; higher values explore more.
Same prompt, three temperatures — watch how the answers diverge.
"""

    temperature = '''prompt = (
    "Invent a name and a one-line pitch for an AI-powered study tool "
    "that helps students read model papers."
)
for temp in (0.0, 0.7, 1.2):
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temp,
        max_tokens=80,
    )
    print(f"--- temperature={temp}")
    print(r.choices[0].message.content.strip())
    print()'''

    compare_md = """### 5. Concept check (prompt the model, then judge it)

Ask a precise question tied to *this* model's story. Then grade the answer against
the **What's the innovation?** section — did it miss data scale, openness, or MoE routing?
"""

    # Tailor the concept-check prompt to the model family a bit.
    if "OLMoE" in entry.id or "MoE" in entry.params:
        concept_q = (
            "Explain Mixture-of-Experts in simple terms. What is the difference "
            "between total parameters and active parameters, and why might that "
            "help run larger models on limited hardware?"
        )
    elif "SmolLM" in entry.id:
        concept_q = (
            "What does it mean for a language model release to be 'fully open'? "
            "List weights, data, code, and documentation — and why each matters."
        )
    else:
        concept_q = (
            "According to your training story, what is special about Qwen2.5's "
            "recipe for small models? Mention pretraining scale and post-training "
            "in plain language."
        )

    concept = f'''r = client.chat.completions.create(
    model=MODEL,
    messages=[{{"role": "user", "content": {concept_q!r}}}],
    max_tokens=220,
    temperature=0.3,
)
print(r.choices[0].message.content)
print("\\n---\\nNow compare this answer with the innovation section at the top of the notebook.")'''

    bench_md = """### 6. Mini benchmark

Rough local throughput: **TTFT** (time to first token) and **tok/s** (decode speed).
Numbers vary with GPU load — the point is to build intuition, not publish a leaderboard.
"""

    benchmark = '''import time

t0 = time.perf_counter()
first = None
tokens = 0
stream = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Count from 1 to 40, separated by commas."}],
    max_tokens=120,
    temperature=0.0,
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        if first is None:
            first = time.perf_counter()
        tokens += 1
assert first is not None, "no tokens received"
gen_time = time.perf_counter() - first
print(f"TTFT: {first - t0:.3f}s")
print(f"Decode: {tokens} tokens in {gen_time:.2f}s -> {tokens / gen_time:.1f} tok/s")'''

    wrap = f"""## Next steps

- Re-read the paper link for **{entry.name}** — one section is enough.
- Generate the notebook for a *second* model and compare answers on the same prompts.
- Try the **Benchmark** tab for a more careful TTFT / tok/s measurement.
- *(Later)* Slide summaries via reveal.js are on the mxstudio roadmap — for now the
  notebook *is* the lesson.

You finished the lab when you can explain the **key ideas** list in your own words.
"""

    cells = [
        _md(title),
        _md(what),
        _md(innovation),
        _md(architecture),
        _md(resources),
        _md(hf_md),
        _md(lab_intro),
        _md(setup_md),
        _code(setup),
        _md(teach_md),
        _code(teach),
        _md(stream_md),
        _code(streaming),
        _md(temp_md),
        _code(temperature),
        _md(compare_md),
        _code(concept),
        _md(bench_md),
        _code(benchmark),
        _md(wrap),
    ]

    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
            "mxstudio": {
                "model_id": entry.id,
                "kind": "educational_lab",
            },
        },
        "cells": cells,
    }


def write_notebook(entry: ModelEntry) -> Path:
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    slug = entry.id.split("/")[-1].lower()
    path = NOTEBOOKS_DIR / f"{slug}.ipynb"
    path.write_text(json.dumps(build_notebook(entry), indent=1))
    return path


def list_notebooks() -> list[dict]:
    if not NOTEBOOKS_DIR.is_dir():
        return []
    return [
        {"name": p.name, "path": str(p)}
        for p in sorted(NOTEBOOKS_DIR.glob("*.ipynb"))
    ]


def _safe_path(name: str) -> Path:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("invalid notebook name")
    path = (NOTEBOOKS_DIR / name).resolve()
    if path.parent != NOTEBOOKS_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def load_notebook(name: str) -> dict:
    path = _safe_path(name)
    nb = json.loads(path.read_text())
    cells = []
    for i, cell in enumerate(nb.get("cells", [])):
        cells.append(
            {
                "index": i,
                "cell_type": cell.get("cell_type", "markdown"),
                "source": _cell_source(cell),
            }
        )
    return {"name": name, "path": str(path), "cells": cells}


class _NotebookKernel:
    """One long-lived Python process so variables survive across cells."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen:
        if self._proc and self._proc.poll() is None:
            return self._proc
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _KERNEL_BOOTSTRAP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(PROJECT_ROOT),
        )
        return self._proc

    def reset(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def run(self, code: str, timeout: float = 180.0) -> dict:
        with self._lock:
            proc = self._ensure()
            assert proc.stdin and proc.stdout
            try:
                proc.stdin.write(json.dumps({"code": code}) + "\n")
                proc.stdin.flush()
            except BrokenPipeError:
                self._proc = None
                proc = self._ensure()
                assert proc.stdin and proc.stdout
                proc.stdin.write(json.dumps({"code": code}) + "\n")
                proc.stdin.flush()

            result_q: queue.Queue[str | None] = queue.Queue()

            def _reader() -> None:
                try:
                    line = proc.stdout.readline()
                    result_q.put(line)
                except Exception:
                    result_q.put(None)

            t = threading.Thread(target=_reader, daemon=True)
            t.start()
            try:
                line = result_q.get(timeout=timeout)
            except queue.Empty:
                try:
                    proc.kill()
                except Exception:
                    pass
                self._proc = None
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"cell timed out after {timeout:.0f}s",
                }

            if not line:
                self._proc = None
                return {"ok": False, "stdout": "", "stderr": "kernel died"}
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return {"ok": False, "stdout": "", "stderr": f"bad kernel reply: {line[:200]}"}


_kernels: dict[str, _NotebookKernel] = {}
_kernels_lock = threading.Lock()


def _kernel(name: str) -> _NotebookKernel:
    with _kernels_lock:
        if name not in _kernels:
            _kernels[name] = _NotebookKernel()
        return _kernels[name]


def run_cell(
    name: str,
    cell_index: int,
    code: str | None = None,
    timeout: float = 180.0,
) -> dict:
    nb = load_notebook(name)
    if cell_index < 0 or cell_index >= len(nb["cells"]):
        raise IndexError(f"cell index {cell_index} out of range")
    cell = nb["cells"][cell_index]
    if cell["cell_type"] != "code":
        return {
            "index": cell_index,
            "ok": True,
            "stdout": "",
            "stderr": "",
            "skipped": True,
            "execution_count": None,
        }
    source = code if code is not None else cell["source"]
    started = time.time()
    result = _kernel(name).run(source, timeout=timeout)
    return {
        "index": cell_index,
        "ok": result.get("ok", False),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "elapsed_s": round(time.time() - started, 2),
        "skipped": False,
    }


def run_all(
    name: str,
    cells: list[dict] | None = None,
    timeout_per_cell: float = 180.0,
) -> list[dict]:
    """Run all code cells. Optional `cells` uses in-memory edited sources."""
    nb = load_notebook(name)
    sources = {c["index"]: c for c in (cells or nb["cells"])}
    _kernel(name).reset()
    results = []
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        edited = sources.get(cell["index"], cell)
        results.append(
            run_cell(
                name,
                cell["index"],
                code=edited.get("source"),
                timeout=timeout_per_cell,
            )
        )
        if not results[-1]["ok"]:
            break
    return results


def save_notebook(name: str, cells: list[dict]) -> Path:
    """Rewrite an .ipynb from the in-app cell list (preserves order/types)."""
    path = _safe_path(name)
    nb_cells = []
    for cell in cells:
        ctype = cell.get("cell_type", "markdown")
        source = cell.get("source", "")
        if ctype == "code":
            nb_cells.append(_code(source))
        else:
            nb_cells.append(_md(source))
    doc = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "cells": nb_cells,
    }
    path.write_text(json.dumps(doc, indent=1))
    return path


def reset_kernel(name: str) -> None:
    _kernel(name).reset()


class NotebookJobManager:
    """Run notebook cells in a background thread so the UI never blocks."""

    def __init__(self) -> None:
        self.job: dict = {"state": "idle"}
        self._lock = threading.Lock()

    def start(
        self,
        name: str,
        cell_index: int | None = None,
        code: str | None = None,
        cells: list[dict] | None = None,
    ) -> dict:
        with self._lock:
            if self.job.get("state") == "running":
                return self.job
            self.job = {
                "state": "running",
                "notebook": name,
                "mode": "all" if cell_index is None else "cell",
                "cell_index": cell_index,
                "current_cell": cell_index,
                "results": [],
                "error": None,
                "started_at": time.time(),
            }

        def _worker() -> None:
            try:
                if cell_index is None:
                    # Progressive results so the UI can show cells finishing.
                    nb = load_notebook(name)
                    sources = {c["index"]: c for c in (cells or nb["cells"])}
                    _kernel(name).reset()
                    for cell in nb["cells"]:
                        if cell["cell_type"] != "code":
                            continue
                        self.job["current_cell"] = cell["index"]
                        edited = sources.get(cell["index"], cell)
                        result = run_cell(
                            name,
                            cell["index"],
                            code=edited.get("source"),
                        )
                        self.job["results"].append(result)
                        if not result["ok"]:
                            break
                else:
                    self.job["current_cell"] = cell_index
                    self.job["results"] = [
                        run_cell(name, cell_index, code=code)
                    ]
                self.job["state"] = "done"
            except Exception as exc:
                self.job["state"] = "error"
                self.job["error"] = str(exc)[:500]

        threading.Thread(
            target=_worker, daemon=True, name=f"nb-{name}"
        ).start()
        return self.job

    def status(self) -> dict:
        return self.job


NOTEBOOK_JOBS = NotebookJobManager()
