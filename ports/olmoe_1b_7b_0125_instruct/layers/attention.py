"""OLMoE attention with full projected-vector Q/K normalization."""

from __future__ import annotations

from collections.abc import Iterable

from max.graph import DeviceRef, TensorValue, ops
from max.nn.attention import MHAMaskVariant
from max.nn.kernels import flash_attention_ragged, rope_split_store_ragged
from max.nn.norm import RMSNorm
from max.pipelines.architectures.qwen3.layers.attention import Qwen3Attention


class OlmoeAttention(Qwen3Attention):
    """Qwen3 fused attention operations with OLMoE Q/K norm semantics."""

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        q_dim = self.kv_params.head_dim * self.n_heads
        kv_dim = self.kv_params.head_dim * self.num_key_value_heads
        self.q_norm = RMSNorm(
            q_dim,
            dtype=self.norm_dtype,
            eps=self.qk_norm_eps,
            multiply_before_cast=False,
        )
        self.k_norm = RMSNorm(
            kv_dim,
            dtype=self.norm_dtype,
            eps=self.qk_norm_eps,
            multiply_before_cast=False,
        )

    def shard(self, devices: Iterable[DeviceRef]) -> list[OlmoeAttention]:
        device_list = list(devices)
        if len(device_list) != 1:
            raise ValueError(
                "OLMoE full-vector Q/K normalization currently supports one GPU"
            )
        if not self._sharding_strategy:
            raise ValueError("OlmoeAttention requires a sharding strategy")

        sharded = OlmoeAttention(
            rope=self.rope,
            num_attention_heads=self.n_heads,
            num_key_value_heads=self.num_key_value_heads,
            hidden_size=self.hidden_size,
            kv_params=self.kv_params,
            layer_idx=self.layer_idx,
            dtype=self.dtype,
            devices=device_list,
            linear_cls=self.linear_cls,
            scale=self.scale,
            has_bias=self.has_bias,
            qk_norm_eps=self.qk_norm_eps,
            norm_dtype=self.norm_dtype,
            quant_config=self.quant_config,
        )
        sharded.qkv_proj = self.qkv_proj.shard(device_list)[0]
        sharded.o_proj = self.o_proj.shard(device_list)[0]
        sharded.q_norm = self.q_norm.shard(device_list)[0]
        sharded.k_norm = self.k_norm.shard(device_list)[0]
        return [sharded]

    def __call__(
        self,
        layer_idx: TensorValue,
        x: TensorValue,
        kv_collection,  # type: ignore[no-untyped-def]
        freqs_cis: TensorValue,
        input_row_offsets: TensorValue,
    ) -> TensorValue:
        total_seq_len = x.shape[0]
        head_dim = self.kv_params.head_dim
        q_dim = head_dim * self.n_heads
        kv_dim = head_dim * self.num_key_value_heads

        x_q, x_k, x_v = ops.split(
            self.qkv_proj(x), [q_dim, kv_dim, kv_dim], axis=-1
        )
        x_q = self.q_norm(x_q)
        x_k = self.k_norm(x_k)
        qkv = ops.concat((x_q, x_k, x_v), axis=-1)

        freqs_cis = ops.cast(freqs_cis, qkv.dtype).to(qkv.device)
        xq = rope_split_store_ragged(
            self.kv_params,
            qkv,
            input_row_offsets,
            freqs_cis,
            kv_collection,
            layer_idx,
            n_heads=self.n_heads,
            interleaved=self.rope.interleaved,
        ).reshape((-1, self.n_heads, head_dim))
        attn_out = flash_attention_ragged(
            self.kv_params,
            input=xq,
            kv_collection=kv_collection,
            layer_idx=layer_idx,
            input_row_offsets=input_row_offsets,
            mask_variant=MHAMaskVariant.CAUSAL_MASK,
            scale=self.scale,
        )
        return self.o_proj(ops.reshape(attn_out, [total_seq_len, -1]))