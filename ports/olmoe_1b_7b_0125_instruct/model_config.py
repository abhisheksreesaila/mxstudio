"""MAX configuration mapping for OLMoE."""

from __future__ import annotations

from copy import copy

from max.pipelines.architectures.qwen3.model_config import Qwen3Config
from max.pipelines.lib import MAXModelConfig, PipelineConfig
from transformers import AutoConfig
from typing_extensions import Self, override


class OlmoeConfig(Qwen3Config):
    """Map Hugging Face OLMoE fields onto the MAX Qwen3-MoE runtime."""

    @override
    @classmethod
    def initialize_from_config(
        cls,
        pipeline_config: PipelineConfig,
        huggingface_config: AutoConfig,
        model_config: MAXModelConfig | None = None,
    ) -> Self:
        adapted_config = copy(huggingface_config)
        adapted_config.head_dim = (
            huggingface_config.hidden_size
            // huggingface_config.num_attention_heads
        )
        adapted_config.moe_intermediate_size = (
            huggingface_config.intermediate_size
        )
        return super().initialize_from_config(
            pipeline_config, adapted_config, model_config
        )


CustomModelConfig = OlmoeConfig
