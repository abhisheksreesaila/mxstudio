from __future__ import annotations

import inspect
import unittest

from max.graph.weights import WeightsFormat

from ports.olmoe_1b_7b_0125_instruct import ARCHITECTURES
from ports.olmoe_1b_7b_0125_instruct.layers.moe import OlmoeMoEGate
from ports.olmoe_1b_7b_0125_instruct.model import OlmoeModel
from ports.olmoe_1b_7b_0125_instruct.weight_adapters import (
    convert_safetensor_state_dict,
)


class _FakeWeight:
    def __init__(self, value: object) -> None:
        self.value = value

    def data(self) -> object:
        return self.value


class OlmoePortTests(unittest.TestCase):
    def test_registration_matches_checkpoint_architecture(self) -> None:
        architecture = ARCHITECTURES[0]

        self.assertEqual(architecture.name, "OlmoeForCausalLM")
        self.assertIs(architecture.pipeline_model, OlmoeModel)
        self.assertIn(WeightsFormat.safetensors, architecture.weight_adapters)
        self.assertFalse(architecture.multi_gpu_supported)

    def test_pipeline_builds_olmoe_graph(self) -> None:
        source = inspect.getsource(OlmoeModel._build_graph_for_compile)

        self.assertIn("nn_model = Olmoe(model_config)", source)
        self.assertNotIn("nn_model = Qwen3(model_config)", source)

    def test_router_keeps_selected_probabilities_unnormalized(self) -> None:
        source = inspect.getsource(OlmoeMoEGate.__call__)

        self.assertIn("ops.softmax", source)
        self.assertIn("ops.top_k", source)
        self.assertNotIn("/", source)

    def test_weight_adapter_maps_router_and_strips_model_prefix(self) -> None:
        state_dict = {
            "model.layers.0.mlp.gate.weight": _FakeWeight("router"),
            "model.layers.0.self_attn.q_norm.weight": _FakeWeight("q_norm"),
            "lm_head.weight": _FakeWeight("lm_head"),
        }

        converted = convert_safetensor_state_dict(
            state_dict, None, None  # type: ignore[arg-type]
        )

        self.assertEqual(
            converted["layers.0.mlp.gate.gate_score.weight"], "router"
        )
        self.assertEqual(
            converted["layers.0.self_attn.q_norm.weight"], "q_norm"
        )
        self.assertEqual(converted["lm_head.weight"], "lm_head")


if __name__ == "__main__":
    unittest.main()