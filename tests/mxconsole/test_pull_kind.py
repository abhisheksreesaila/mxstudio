"""A pulled model's kind comes from its Hugging Face card (task and tags) and its name."""
from fakes import CATALOG, make_hf

from mxconsole import resolve as r
from mxconsole.store import Store


def pull(ref, **kw):
    return r.pull(ref, store=Store.open(), catalog=CATALOG, hf=make_hf(), **kw)


def test_a_plain_text_model_is_chat():
    assert pull("qwen2.5-0.5b").kind == "chat"


def test_the_card_task_decides_the_kind():
    assert pull("acme/eyes").kind == "vision"
    assert pull("acme/vectors").kind == "embeddings"


def test_a_code_tag_on_a_text_model_makes_it_a_coder():
    assert pull("acme/helper-7b").kind == "coder"


def test_the_kind_is_stored_and_survives_reading_it_back():
    pull("acme/eyes")
    assert Store.open().get_model("eyes").kind == "vision"
