"""SupportedArchitecture registration for OlmoeForCausalLM."""
from max.graph.weights import WeightsFormat
from max.pipelines.context import TextContext
from max.pipelines.architectures.qwen3.batch_processor import Qwen3BatchProcessor
from max.pipelines.kv_cache.memory_planner import PagedMemoryPlanner
from max.pipelines.lib import SupportedArchitecture, TextTokenizer
from max.pipelines.modeling.types import PipelineTask

from .model import OlmoeModel
from .model_config import OlmoeConfig
from .weight_adapters import convert_safetensor_state_dict

# IMPORTANT: `name` must match config.json architectures[0] exactly.
custom_arch = SupportedArchitecture(
    name="OlmoeForCausalLM",
    task=PipelineTask.TEXT_GENERATION,
    example_repo_ids=["allenai/OLMoE-1B-7B-0125-Instruct"],
    default_weights_format=WeightsFormat.safetensors,
    default_encoding=OlmoeConfig.DEFAULT_ENCODING,
    supported_encodings=OlmoeConfig.SUPPORTED_ENCODINGS,
    pipeline_model=OlmoeModel,
    tokenizer=TextTokenizer,
    context_type=TextContext,
    weight_adapters={
        WeightsFormat.safetensors: convert_safetensor_state_dict,
    },
    config=OlmoeConfig,
    batching=Qwen3BatchProcessor,
    multi_gpu_supported=False,
    memory_planner=PagedMemoryPlanner.with_activation_reservation(
        0, always_signal_buffers=True
    ),
    supports_device_graph_capture=False,
)
