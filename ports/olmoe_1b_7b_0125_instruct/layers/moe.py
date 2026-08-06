"""OLMoE top-k router."""

from __future__ import annotations

from collections.abc import Iterable

from max.dtype import DType
from max.graph import DeviceRef, TensorValue, ops
from max.nn.moe import MoEGate


class OlmoeMoEGate(MoEGate):
    """Softmax over all experts followed by unnormalized top-k selection."""

    norm_topk_prob = False

    def __call__(
        self, hidden_states: TensorValue
    ) -> tuple[TensorValue, TensorValue]:
        router_logits = self.gate_score(hidden_states).cast(DType.float32)
        routing_weights = ops.softmax(router_logits, axis=-1)
        topk_weights, topk_indices = ops.top_k(
            routing_weights, k=self.num_experts_per_token, axis=-1
        )
        return topk_indices, topk_weights.cast(hidden_states.dtype)

    def shard(self, devices: Iterable[DeviceRef]) -> list[OlmoeMoEGate]:
        if not self._sharding_strategy:
            raise ValueError("OlmoeMoEGate requires a sharding strategy")

        device_list = list(devices)
        gate_score_shards = self.gate_score.shard(device_list)
        shards: list[OlmoeMoEGate] = []
        for shard_idx, device in enumerate(device_list):
            sharded = OlmoeMoEGate(
                devices=[device],
                hidden_dim=self.hidden_dim,
                num_experts=self.num_experts,
                num_experts_per_token=self.num_experts_per_token,
                dtype=self.dtype,
                is_sharding=True,
            )
            sharded.gate_score = gate_score_shards[shard_idx]
            shards.append(sharded)
        return shards