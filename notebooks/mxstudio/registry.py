"""Curated model registry: small models known to run on MAX out of the box."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelEntry:
    id: str
    name: str
    params: str
    approx_gb: float
    context: int
    license: str
    description: str
    innovation: str
    links: dict[str, str] = field(default_factory=dict)
    experimental: bool = False
    # Path relative to the mxstudio project root (for example ports/my_model).
    custom_arch: str | None = None
    # Comma-separated CUDA device ids for `max serve` (dual-GPU MoE needs "0,1").
    cuda_devices: str = "0"
    # Educational extras for notebooks
    family: str = ""
    architecture: str = ""
    when_to_use: str = ""
    key_ideas: tuple[str, ...] = ()
    learning_goals: tuple[str, ...] = ()
    # Explicitly hide Serve for parked entries with known-incomplete adapters.
    serve_enabled: bool = True
    serve_disabled_reason: str = ""
    # Optional immutable local checkpoint identity; canonical `id` remains public.
    load_path: str | None = None
    revision: str | None = None
    advertised_model_id: str | None = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["key_ideas"] = list(self.key_ideas)
        d["learning_goals"] = list(self.learning_goals)
        return d


REGISTRY: list[ModelEntry] = [
    ModelEntry(
        id="Qwen/Qwen2.5-0.5B-Instruct",
        name="Qwen2.5 0.5B Instruct",
        params="0.5B",
        approx_gb=1.0,
        context=32768,
        license="Apache 2.0",
        family="Qwen2.5 (Alibaba)",
        description=(
            "The smallest member of the Qwen2.5 family. Downloads in seconds and "
            "compiles fast, making it the ideal smoke-test and demo model."
        ),
        innovation=(
            "Qwen2.5 pushed small-model quality by pretraining on ~18T tokens with "
            "aggressive data filtering, then applying the same post-training recipe "
            "(supervised finetuning + multi-stage reinforcement learning) used by the "
            "flagship models. The surprising result: even a 0.5B checkpoint keeps "
            "32K context and usable instruction following — proof that data quality "
            "and post-training transfer down the size ladder."
        ),
        architecture=(
            "Dense decoder-only transformer (Qwen2 architecture): grouped-query "
            "attention (GQA), SwiGLU feed-forward layers, RoPE positional embeddings, "
            "and a large vocabulary (~150k) tokenizer that helps multilingual and "
            "code performance. The Instruct variant is chat-tuned with a ChatML-style "
            "template."
        ),
        when_to_use=(
            "Use this when you want a fast local sandbox: UI demos, notebook labs, "
            "API integration tests, or teaching the OpenAI chat protocol without "
            "waiting on a large download. Do not expect strong multi-step reasoning "
            "or complex coding — graduate to 1.5B/3B for that."
        ),
        key_ideas=(
            "Small models can inherit the post-training recipe of larger siblings",
            "Long context (32K) is no longer reserved for huge models",
            "Instruction tuning matters as much as raw parameter count at this size",
        ),
        learning_goals=(
            "Explain what makes Qwen2.5 different from earlier small open models",
            "Call a local OpenAI-compatible endpoint and stream tokens",
            "Measure how temperature changes creativity vs. determinism",
        ),
        links={
            "Paper (Qwen2.5 Technical Report)": "https://arxiv.org/abs/2412.15115",
            "GitHub": "https://github.com/QwenLM/Qwen2.5",
            "Hugging Face": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct",
        },
    ),
    ModelEntry(
        id="Qwen/Qwen2.5-1.5B-Instruct",
        name="Qwen2.5 1.5B Instruct",
        params="1.5B",
        approx_gb=3.1,
        context=32768,
        license="Apache 2.0",
        family="Qwen2.5 (Alibaba)",
        description=(
            "Sweet spot between speed and quality on a single consumer GPU. "
            "Noticeably better reasoning and coding than the 0.5B."
        ),
        innovation=(
            "Same Qwen2.5 recipe as the flagship: ~18T-token pretraining, improved "
            "structured-output (JSON) generation, and multilingual coverage of 29+ "
            "languages — packed into a 1.5B checkpoint that typically fits in ~3GB of "
            "VRAM. It is often the best 'daily driver' small model when you want "
            "quality without a 7B download."
        ),
        architecture=(
            "Dense Qwen2 decoder-only transformer with GQA, SwiGLU, RoPE, and the "
            "Qwen2.5 tokenizer/chat template. Same family design as 0.5B/3B — only "
            "width and depth scale."
        ),
        when_to_use=(
            "Good default for local assistants, light coding help, and notebook "
            "experiments where 0.5B feels too weak but 3B is more than you need."
        ),
        key_ideas=(
            "Scaling laws still apply inside a family — 1.5B sits on a steep part of the curve",
            "Structured output (JSON) was an explicit Qwen2.5 training focus",
            "Multilingual coverage is a first-class goal, not an afterthought",
        ),
        learning_goals=(
            "Compare 0.5B vs 1.5B qualitatively on the same prompts",
            "Practice streaming and generation controls (temperature, top-p)",
            "Skim the technical report sections on post-training",
        ),
        links={
            "Paper (Qwen2.5 Technical Report)": "https://arxiv.org/abs/2412.15115",
            "GitHub": "https://github.com/QwenLM/Qwen2.5",
            "Hugging Face": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct",
        },
    ),
    ModelEntry(
        id="Qwen/Qwen2.5-3B-Instruct",
        name="Qwen2.5 3B Instruct",
        params="3B",
        approx_gb=6.2,
        context=32768,
        license="Qwen Research",
        family="Qwen2.5 (Alibaba)",
        description=(
            "The model mxstudio was first benchmarked with (~35 tok/s on an "
            "RTX 3090). Strong general assistant quality while leaving VRAM to spare."
        ),
        innovation=(
            "Qwen2.5's mid-size open checkpoint: the full 18T-token pretraining + "
            "SFT + staged RL stack, delivering near GPT-3.5-class chat quality for "
            "many everyday tasks on a single consumer GPU. It is the practical "
            "ceiling of the 'small but serious' Qwen2.5 line before you jump to 7B+."
        ),
        architecture=(
            "Dense Qwen2 decoder-only transformer (GQA, SwiGLU, RoPE). Instruct "
            "checkpoint uses the Qwen chat template. License is more restrictive "
            "than the Apache 2.0 0.5B/1.5B variants — check the card before shipping."
        ),
        when_to_use=(
            "Local coding help, longer answers, and demos where quality matters. "
            "Still small enough that MAX compile times stay reasonable on a 3090."
        ),
        key_ideas=(
            "Post-training (SFT + RL) is a major share of perceived 'smartness'",
            "3B is often enough for interactive assistants on consumer GPUs",
            "License differences inside a family are easy to miss — always check",
        ),
        learning_goals=(
            "Run a throughput benchmark and interpret TTFT vs tok/s",
            "Read the Qwen2.5 report's discussion of data and post-training",
            "Contrast open vs research licenses for deployment decisions",
        ),
        links={
            "Paper (Qwen2.5 Technical Report)": "https://arxiv.org/abs/2412.15115",
            "GitHub": "https://github.com/QwenLM/Qwen2.5",
            "Hugging Face": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct",
        },
    ),
    ModelEntry(
        id="HuggingFaceTB/SmolLM2-1.7B-Instruct",
        name="SmolLM2 1.7B Instruct",
        params="1.7B",
        approx_gb=3.4,
        context=8192,
        license="Apache 2.0",
        family="SmolLM2 (Hugging Face)",
        description=(
            "Hugging Face's fully-open small model (open data, open training "
            "recipe). A nice contrast to Qwen for side-by-side comparisons."
        ),
        innovation=(
            "SmolLM2 is a study in data-centric training: ~11T tokens with new "
            "curated datasets (FineMath, Stack-Edu, SmolTalk) and multi-stage data "
            "rebalancing. Unlike many 'open weights' releases, the paper and "
            "datasets document the full recipe — making it one of the most "
            "reproducible small models available."
        ),
        architecture=(
            "Dense decoder-only transformer in the SmolLM lineage. The Instruct "
            "variant is tuned on SmolTalk and related chat/instruction mixtures. "
            "Context is 8K — shorter than Qwen2.5's 32K, which is a useful "
            "trade-off to discuss in class."
        ),
        when_to_use=(
            "Teaching openness and reproducibility; comparing data recipes against "
            "Qwen; Apache 2.0 deployments where you want a fully open stack."
        ),
        key_ideas=(
            "Open weights ≠ open science — SmolLM2 aims for both",
            "Dataset curation (math, education, chat) beats brute-force scraping alone",
            "Side-by-side eval with Qwen teaches more than either model alone",
        ),
        learning_goals=(
            "Articulate what 'fully open' means (weights, data, code, paper)",
            "Compare SmolLM2 and Qwen2.5 on the same prompts",
            "Skim the SmolLM2 paper's data mixture section",
        ),
        links={
            "Paper (SmolLM2)": "https://arxiv.org/abs/2502.02737",
            "GitHub": "https://github.com/huggingface/smollm",
            "Hugging Face": "https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct",
        },
    ),
    ModelEntry(
        id="google/gemma-3-1b-it",
        name="Gemma 3 1B Instruct",
        params="1B",
        approx_gb=2.0,
        context=32768,
        license="Gemma (gated)",
        family="Gemma 3 (Google)",
        description=(
            "Google's compact multilingual instruction model. The 1B text model "
            "keeps a 32K context window and requires accepting the Gemma terms."
        ),
        innovation=(
            "Gemma 3 transfers lessons from the Gemini program into an open-weight "
            "family trained across 140+ languages. The 1B edition is text-only, "
            "which makes it a practical way to study the family on modest hardware."
        ),
        architecture=(
            "Dense Gemma 3 decoder-only transformer (Gemma3ForCausalLM) with "
            "grouped-query attention, RoPE, and a 32K input context."
        ),
        when_to_use=(
            "Use for multilingual chat, summarization, and constrained-device "
            "experiments when you can accept Google's Gemma license."
        ),
        key_ideas=(
            "A 1B model can retain broad multilingual coverage",
            "The smallest Gemma 3 is text-only and uses a shorter context than larger siblings",
            "Gated access and open weights are separate properties",
        ),
        learning_goals=(
            "Compare Gemma 3 and OLMo 2 at roughly the same size",
            "Inspect how a 32K context affects memory use",
            "Recognize gated model-download workflows",
        ),
        links={
            "Technical report": "https://arxiv.org/abs/2503.19786",
            "Gemma docs": "https://ai.google.dev/gemma/docs/core",
            "Hugging Face": "https://huggingface.co/google/gemma-3-1b-it",
        },
    ),
    ModelEntry(
        id="meta-llama/Llama-3.2-1B-Instruct",
        name="Llama 3.2 1B Instruct",
        params="1.23B",
        approx_gb=2.5,
        context=131072,
        license="Llama 3.2 (gated)",
        family="Llama 3.2 (Meta)",
        description=(
            "A mobile-oriented multilingual Llama with a 128K context window. "
            "Hugging Face access requires accepting Meta's Llama 3.2 license."
        ),
        innovation=(
            "Meta created the 1B and 3B Llama 3.2 models through pruning and "
            "knowledge distillation from larger Llama 3.1 teachers, targeting "
            "assistant and agentic workloads in constrained environments."
        ),
        architecture=(
            "Dense Llama decoder-only transformer (LlamaForCausalLM) with "
            "grouped-query attention, SwiGLU feed-forward layers, and RoPE."
        ),
        when_to_use=(
            "Use for long-context retrieval, summarization, and multilingual "
            "assistant experiments where ecosystem compatibility matters."
        ),
        key_ideas=(
            "Distillation can compress a larger model's behavior into a mobile-scale checkpoint",
            "Grouped-query attention improves inference efficiency",
            "A 128K advertised context still carries a substantial KV-cache cost",
        ),
        learning_goals=(
            "Contrast parameter memory with long-context KV-cache memory",
            "Compare distilled and conventionally pretrained small models",
            "Review the obligations of a community model license",
        ),
        links={
            "Llama 3.2 model card": "https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/MODEL_CARD.md",
            "Llama recipes": "https://github.com/meta-llama/llama-recipes",
            "Hugging Face": "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct",
        },
    ),
    ModelEntry(
        id="microsoft/Phi-3.5-mini-instruct",
        name="Phi-3.5 Mini Instruct",
        params="3.8B",
        approx_gb=7.6,
        context=131072,
        license="MIT",
        family="Phi-3.5 (Microsoft)",
        description=(
            "A compact 3.8B reasoning model with 128K context, strong code and "
            "math performance, and a permissive MIT license."
        ),
        innovation=(
            "Phi-3.5 emphasizes reasoning-dense synthetic and carefully filtered "
            "public data, followed by SFT, PPO, and DPO. It is a useful example of "
            "how training-data quality can make a small model unusually capable."
        ),
        architecture=(
            "Dense Phi-3 decoder-only transformer (Phi3ForCausalLM) with full "
            "multi-head attention and LongRoPE scaling for 128K context."
        ),
        when_to_use=(
            "Use for local code, math, logic, or long-document experiments when "
            "you want more capability than a 1B model without moving to 7B."
        ),
        key_ideas=(
            "Reasoning-dense data can outperform indiscriminate scale",
            "LongRoPE extends a model trained from a shorter original context",
            "Small models remain useful when latency and memory are hard constraints",
        ),
        learning_goals=(
            "Test long-context retrieval at increasing prompt lengths",
            "Compare code and math quality with similarly sized Qwen models",
            "Inspect the effect of deterministic decoding on reasoning tasks",
        ),
        links={
            "Phi-3 technical report": "https://arxiv.org/abs/2404.14219",
            "Phi-3 cookbook": "https://github.com/microsoft/Phi-3CookBook",
            "Hugging Face": "https://huggingface.co/microsoft/Phi-3.5-mini-instruct",
        },
    ),
    ModelEntry(
        id="ibm-granite/granite-3.3-2b-instruct",
        name="Granite 3.3 2B Instruct",
        params="2B",
        approx_gb=5.1,
        context=131072,
        license="Apache 2.0",
        family="Granite 3.3 (IBM)",
        description=(
            "IBM's compact 128K business and RAG model, tuned for instruction "
            "following, function calling, code, and structured reasoning."
        ),
        innovation=(
            "Granite 3.3 combines permissively licensed public data with curated "
            "synthetic reasoning tasks. It can separate reasoning and responses "
            "with explicit tags while remaining small enough for local deployment."
        ),
        architecture=(
            "Dense Granite decoder-only transformer (GraniteForCausalLM) with GQA, "
            "RoPE, tied embeddings, and 40 transformer layers."
        ),
        when_to_use=(
            "Use for enterprise assistants, RAG, extraction, function calling, "
            "and long-document workflows under Apache 2.0."
        ),
        key_ideas=(
            "Small models can be specialized for business and retrieval workflows",
            "Structured reasoning can be exposed separately from final responses",
            "Permissive data and licensing simplify downstream adoption",
        ),
        learning_goals=(
            "Build a grounded RAG prompt and inspect citation behavior",
            "Exercise the model's function-calling template",
            "Compare hidden reasoning enabled and disabled",
        ),
        links={
            "Granite docs": "https://www.ibm.com/granite/docs/",
            "GitHub": "https://github.com/ibm-granite/granite-3.3-language-models",
            "Hugging Face": "https://huggingface.co/ibm-granite/granite-3.3-2b-instruct",
        },
    ),
    ModelEntry(
        id="mistralai/Mistral-7B-Instruct-v0.3",
        name="Mistral 7B Instruct v0.3",
        params="7B",
        approx_gb=14.5,
        context=32768,
        license="Apache 2.0",
        family="Mistral 7B (Mistral AI)",
        description=(
            "A widely used 7B instruction model with a 32K context window, v3 "
            "tokenizer, and function-calling support."
        ),
        innovation=(
            "Mistral 7B established a strong quality-per-parameter baseline using "
            "grouped-query attention. Version 0.3 extends the vocabulary and adds "
            "a tool-aware chat template while retaining a permissive license."
        ),
        architecture=(
            "Dense Mistral decoder-only transformer (MistralForCausalLM) with GQA, "
            "SwiGLU layers, RoPE, and eight key/value heads."
        ),
        when_to_use=(
            "Use when 7B quality, broad ecosystem support, function calling, and "
            "Apache licensing matter more than minimum download size."
        ),
        key_ideas=(
            "Grouped-query attention reduces KV-cache bandwidth and memory",
            "Chat templates carry tool schemas as part of the prompt contract",
            "7B remains a useful reference point for local model quality",
        ),
        learning_goals=(
            "Implement a complete function-call and tool-result cycle",
            "Benchmark a 7B model against the compact catalog entries",
            "Measure KV-cache growth across context lengths",
        ),
        links={
            "Mistral inference": "https://github.com/mistralai/mistral-inference",
            "Hugging Face": "https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.3",
        },
    ),
    ModelEntry(
        id="allenai/OLMo-2-0425-1B-Instruct",
        name="OLMo 2 1B Instruct",
        params="1B",
        approx_gb=2.4,
        context=4096,
        license="Apache 2.0",
        family="OLMo 2 (Ai2)",
        description=(
            "Ai2's fully open 1B instruction model, released with training code, "
            "data details, checkpoints, logs, and an Apache 2.0 license."
        ),
        innovation=(
            "OLMo 2 is designed for reproducible language-model science. This "
            "checkpoint adds SFT, DPO, and reinforcement learning with verifiable "
            "rewards while preserving unusually complete training transparency."
        ),
        architecture=(
            "Dense OLMo 2 decoder-only transformer (Olmo2ForCausalLM) with 16 "
            "layers, full multi-head attention, RoPE, and post-norm blocks."
        ),
        when_to_use=(
            "Use for education, reproducibility studies, and controlled comparisons "
            "where access to the full training story matters."
        ),
        key_ideas=(
            "Open science includes data, code, logs, and intermediate checkpoints",
            "Post-training stages can be studied independently",
            "A short 4K context trades flexibility for a small runtime footprint",
        ),
        learning_goals=(
            "Trace the checkpoint's SFT, DPO, and RLVR lineage",
            "Compare OLMo 2 with the sparse OLMoE architecture",
            "Reproduce a small evaluation with documented inputs",
        ),
        links={
            "Paper (OLMo 2)": "https://arxiv.org/abs/2501.00656",
            "OLMo core": "https://github.com/allenai/OLMo-core",
            "Hugging Face": "https://huggingface.co/allenai/OLMo-2-0425-1B-Instruct",
        },
    ),
    ModelEntry(
        id="Qwen/Qwen3-30B-A3B-Instruct-2507-FP8",
        name="Qwen3 30B-A3B Instruct (FP8)",
        params="30B total / 3B active (MoE, FP8)",
        approx_gb=31.2,
        context=262144,
        license="Apache 2.0",
        family="Qwen3 MoE (Alibaba)",
        experimental=True,
        cuda_devices="0,1",
        description=(
            "MAX-native MoE (downloaded). Dual-GPU memory plan OK, but FP8 grouped "
            "matmul requires SM90/SM100 (H100+) — RTX 3090 is SM86 so graph compile "
            "fails. Kept as the streaming target for Hopper/Blackwell hosts."
        ),
        innovation=(
            "Qwen3-30B-A3B keeps only ~3B parameters active per token while storing "
            "30B total across experts. That sparsity is what makes disk streaming "
            "interesting: most expert weights are idle on any given forward pass. "
            "The FP8 checkpoint is first-class in Modular MAX."
        ),
        architecture=(
            "Sparse MoE decoder (Qwen3MoeForCausalLM): dense attention + routed expert "
            "MLPs. MAX ships a weight adapter that stacks per-expert tensors into the "
            "StackedMoE layout. Serve with tensor parallel across two GPUs."
        ),
        when_to_use=(
            "Streaming / MoE systems work on this machine. Not for single-GPU demos — "
            "use Qwen2.5 0.5B/3B for that."
        ),
        key_ideas=(
            "Active params ≪ total params is the MoE efficiency bet",
            "MAX-native MoE avoids custom-architecture detours",
            "Streaming only pays off when idle experts dominate weight size",
        ),
        learning_goals=(
            "Serve a MAX-native MoE on dual GPU",
            "Contrast active vs total parameter counts",
            "Measure the trade-offs of sparse expert activation",
        ),
        links={
            "Hugging Face": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507-FP8",
            "Qwen3 blog": "https://qwenlm.github.io/blog/qwen3/",
        },
    ),
    ModelEntry(
        id="allenai/OLMoE-1B-7B-0125-Instruct",
        name="OLMoE 1B-7B Instruct",
        params="7B total / 1B active (MoE)",
        approx_gb=13.8,
        context=4096,
        license="Apache 2.0",
        family="OLMoE (Ai2)",
        experimental=True,
        custom_arch="ports/olmoe_1b_7b_0125_instruct",
        serve_enabled=True,
        description=(
            "Experimental custom MAX port for the fully open Ai2 mixture-of-experts "
            "model. Compile, strict weight loading, and local chat are verified on "
            "one GPU; numerical parity validation remains pending."
        ),
        innovation=(
            "OLMoE showed that a sparse MoE with only ~1B active parameters can "
            "match much larger dense models — and released everything: weights, "
            "data, training code, and router analyses. Expert sparsity is exactly "
            "what makes streaming-from-disk viable: only the routed experts need "
            "to be resident in memory for each token."
        ),
        architecture=(
            "Mixture-of-Experts transformer: dense attention + MoE MLP blocks with "
            "64 experts/layer and top-8 routing (~1B active / ~7B total params). "
            "Because most experts are idle on any given token, expert placement and "
            "caching are useful systems-research questions."
        ),
        when_to_use=(
            "Learning MoE routing; experimenting with expert streaming; comparing "
            "sparse vs dense efficiency. Keep to one GPU while the full-projection "
            "Q/K normalization path remains intentionally non-sharded."
        ),
        key_ideas=(
            "MoE = more parameters without paying the full dense FLOPs cost",
            "Router decisions determine which expert weights must be loaded",
            "Open MoE releases unlock systems research (caching, prefetch, SSD tiering)",
        ),
        learning_goals=(
            "Explain top-k routing and why it enables SSD streaming",
            "Contrast active vs total parameter counts",
            "Read the OLMoE paper's motivation for fully open MoE research",
        ),
        links={
            "Paper (OLMoE)": "https://arxiv.org/abs/2409.02060",
            "GitHub": "https://github.com/allenai/OLMoE",
            "Hugging Face": "https://huggingface.co/allenai/OLMoE-1B-7B-0125-Instruct",
        },
    ),
]


def get(model_id: str) -> ModelEntry | None:
    for entry in REGISTRY:
        if entry.id == model_id:
            return entry
    return None


def resolve(query: str) -> ModelEntry | None:
    """Resolve an exact id or a unique case-insensitive substring."""
    exact = get(query)
    if exact:
        return exact
    q = query.lower()
    matches = [e for e in REGISTRY if q in e.id.lower() or q in e.name.lower()]
    return matches[0] if len(matches) == 1 else None
