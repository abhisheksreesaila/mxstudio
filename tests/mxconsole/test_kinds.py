"""A model's kind comes from its Hugging Face card; a label name is lower-case [a-z0-9-]."""
import pytest

from mxconsole.kinds import LabelError, check_label, derive_kind


@pytest.mark.parametrize("pipeline_tag,tags,name,arch,kind", [
    ("text-generation", (), "qwen2.5-0.5b", "Qwen2ForCausalLM", "chat"),
    ("text-generation", (), "qwen2.5-coder-7b", "Qwen2ForCausalLM", "coder"),
    ("text-generation", ("code",), "mystery", "LlamaForCausalLM", "coder"),
    ("text-generation", (), "decoder-only-demo", "LlamaForCausalLM", "chat"),
    ("image-text-to-text", (), "gemma-3", "Gemma3ForConditionalGeneration", "vision"),
    ("image-to-text", (), "x", "X", "vision"),
    ("feature-extraction", (), "bge-small", "BertModel", "embeddings"),
    ("sentence-similarity", (), "x", "X", "embeddings"),
    ("text-to-image", (), "flux", "X", "image"),
    (None, (), "qwen2.5-coder-7b", "Qwen2ForCausalLM", "coder"),
    (None, (), "qwen2-vl-7b", "Qwen2VLForConditionalGeneration", "vision"),
    (None, (), "bge-m3", "XLMRobertaModel", "embeddings"),
    (None, (), "stable-diffusion-xl", "X", "image"),
    (None, (), "tiny", "Qwen2ForCausalLM", "chat"),
    ("translation", (), "tiny", "X", "chat"),
])
def test_derive_kind(pipeline_tag, tags, name, arch, kind):
    assert derive_kind(name=name, architecture=arch, pipeline_tag=pipeline_tag, tags=tags) == kind


@pytest.mark.parametrize("bad", ["", "Work", "my label", "a_b", "-x", "x-", "a" * 41, "é"])
def test_bad_label_names_are_refused_with_a_clear_message(bad):
    with pytest.raises(LabelError) as e:
        check_label(bad)
    assert "lower-case" in str(e.value) and e.value.code == "invalid_label"


def test_good_label_names_pass():
    assert [check_label(n) for n in ("work", "a1", "my-label-2")] == ["work", "a1", "my-label-2"]
