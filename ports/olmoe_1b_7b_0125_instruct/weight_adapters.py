"""Safetensor weight-name conversion for OLMoE."""

from max.graph.weights import WeightData, Weights
from max.pipelines.lib import PipelineConfig
from transformers import AutoConfig


def convert_safetensor_state_dict(
    state_dict: dict[str, Weights],
    huggingface_config: AutoConfig,
    pipeline_config: PipelineConfig,
    **unused_kwargs,
) -> dict[str, WeightData]:
    del huggingface_config, pipeline_config, unused_kwargs
    converted: dict[str, WeightData] = {}
    for checkpoint_name, weight in state_dict.items():
        max_name = checkpoint_name.removeprefix("model.")
        max_name = max_name.replace(
            "mlp.gate.weight", "mlp.gate.gate_score.weight"
        )
        converted[max_name] = weight.data()
    return converted