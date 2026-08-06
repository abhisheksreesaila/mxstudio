"""MAX pipeline and compute graph for OLMoE."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from max.engine import InferenceSession
from max.graph import Graph, ShardingStrategy
from max.nn.comm.ep import EPBatchManager
from max.nn.layer import LayerList
from max.nn.linear import Linear, MLP
from max.nn.moe import MoE, MoEQuantized
from max.nn.norm import RMSNorm
from max.nn.rotary_embedding import Llama3RotaryEmbedding
from max.pipelines.architectures.qwen3.model import Qwen3Model
from max.pipelines.architectures.qwen3.qwen3 import (
  Qwen3,
  Qwen3TransformerBlock,
)
from typing_extensions import override

from .layers import OlmoeAttention, OlmoeMoEGate
from .model_config import OlmoeConfig


class OlmoeTransformerBlock(Qwen3TransformerBlock):
  """Pre-norm attention and sparse MoE decoder block."""

  def __init__(
    self,
    config: OlmoeConfig,
    layer_idx: int,
    rope: Llama3RotaryEmbedding,
    create_norm: Callable[..., RMSNorm],
    linear_cls: Callable[..., Linear],
    ep_manager: EPBatchManager | None = None,
  ) -> None:
    super().__init__(
      config,
      layer_idx,
      rope,
      create_norm,
      linear_cls,
      ep_manager,
    )
    self.self_attn = OlmoeAttention(
      num_attention_heads=config.num_attention_heads,
      num_key_value_heads=config.num_key_value_heads,
      hidden_size=config.hidden_size,
      kv_params=config.kv_params,
      layer_idx=layer_idx,
      dtype=config.dtype,
      rope=rope,
      linear_cls=linear_cls,
      devices=config.devices,
      scale=config.attention_multiplier,
      has_bias=config.attention_bias,
      qk_norm_eps=config.rms_norm_eps,
      norm_dtype=config.norm_dtype or config.dtype,
      quant_config=config.quant_config,
    )
    self.self_attn.sharding_strategy = ShardingStrategy.tensor_parallel(
      len(config.devices)
    )
    self.self_attn_shards = self.self_attn.shard(config.devices)

  def _get_mlp(
    self,
    config: OlmoeConfig,
    layer_idx: int,
    linear_cls: Callable[..., Linear],
    ep_manager: EPBatchManager | None = None,
  ) -> MLP | MoE:
    del layer_idx, linear_cls
    ep_size = (
      config.ep_config.n_gpus_per_node * config.ep_config.n_nodes
      if config.ep_config is not None
      else 1
    )
    moe_cls = MoEQuantized if config.quant_config is not None else MoE
    return moe_cls(
      devices=config.devices,
      hidden_dim=config.hidden_size,
      num_experts=config.num_experts,
      num_experts_per_token=config.num_experts_per_tok,
      moe_dim=config.moe_intermediate_size,
      gate_cls=OlmoeMoEGate,
      dtype=config.dtype,
      quant_config=config.quant_config,
      ep_size=ep_size,
      ep_batch_manager=ep_manager,
    )


class Olmoe(Qwen3):
  """OLMoE graph using the Qwen3 serving and logits pipeline."""

  def __init__(
    self, config: OlmoeConfig, ep_manager: EPBatchManager | None = None
  ) -> None:
    super().__init__(config, ep_manager=ep_manager)
    self.layers = LayerList(
      [
        OlmoeTransformerBlock(
          config=config,
          layer_idx=layer_idx,
          rope=self.rope,
          create_norm=lambda: RMSNorm(
            config.hidden_size,
            dtype=config.norm_dtype or config.dtype,
            eps=config.rms_norm_eps,
            multiply_before_cast=False,
          ),
          linear_cls=self.layers[0].self_attn.linear_cls,
          ep_manager=ep_manager,
        )
        for layer_idx in range(config.num_hidden_layers)
      ]
    )


class OlmoeModel(Qwen3Model):
  """Pipeline model that compiles the OLMoE graph."""

  model_config_cls = OlmoeConfig

  @override
  def _create_model_config(self, state_dict: dict[str, Any]) -> OlmoeConfig:
    model_config = OlmoeConfig.initialize_from_config(
      self.pipeline_config, self.huggingface_config
    )
    model_config.finalize(
      huggingface_config=self.huggingface_config,
      state_dict=state_dict,
      return_logits=self.return_logits,
      norm_method=self.norm_method,
      attention_bias=self.attention_bias,
    )
    return model_config

  @override
  def _build_graph_for_compile(
    self,
    session: InferenceSession,
    state_dict: dict[str, Any],
    model_config: OlmoeConfig,
  ) -> tuple[Graph, dict[str, Any]]:
    del session
    nn_model = Olmoe(model_config)
    graph_inputs = nn_model.input_types(self.kv_params)
    nn_model.load_state_dict(
      state_dict,
      override_quantization_encoding=True,
      weight_alignment=1,
      strict=True,
    )
    weights_registry = nn_model.state_dict()

    with Graph("olmoe", input_types=graph_inputs) as graph:
      tokens, input_row_offsets, return_n_logits, *variadic_args = (
        graph.inputs
      )
      num_devices = len(self.devices)
      signal_buffers = [
        value.buffer for value in variadic_args[:num_devices]
      ]
      kv_collections = self._unflatten_kv_inputs(
        variadic_args[num_devices:]
      )
      outputs = nn_model(
        tokens.tensor,
        kv_collections,
        return_n_logits.tensor,
        input_row_offsets.tensor,
        signal_buffers,
      )
      graph.output(*outputs)

    return graph, weights_registry


CustomPipelineModel = OlmoeModel
