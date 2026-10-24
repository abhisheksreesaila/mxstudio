"""Leftovers from the omxconsole reskin (docs/goals/omxconsole-finish.md, M1)."""
from test_console_blades import needs_node, node


@needs_node
def test_the_tokens_card_never_says_no_traffic_beside_a_request_count():
    quiet = node("m.quietNote('idle', 21)", "model-page.js")
    assert "No traffic" not in quiet["title"] and "21 requests" in quiet["text"]
    assert node("m.quietNote('idle', 0)", "model-page.js")["title"] == "No traffic yet"
    assert node("m.quietNote('running', 0)", "model-page.js")["title"] == "No traffic yet"


@needs_node
def test_the_quiet_note_is_honest_in_every_state():
    """Review of the finish batch: each state the overview can be in gets its own true line."""
    note = lambda st, n: node(f"m.quietNote('{st}', {n})", "model-page.js")
    assert "No traffic" not in note("running", 21)["title"] and "21 requests" in note("running", 21)["text"]
    assert "loads again" not in note("starting", 21)["text"] and note("starting", 21)["title"].startswith("Starting")
    assert "loads again" not in note("crashed", 21)["text"] and "21 requests" in note("crashed", 21)["text"]
    assert note("crashed", 0)["title"] != "No traffic yet" or "Restart" in note("crashed", 0)["text"]


@needs_node
def test_an_empty_benchmark_offers_one_run_button():
    assert node("m.headerRunShown(null, false)", "blade-bench.js") is False      # empty: the empty state's button is the one
    assert node("m.headerRunShown({id: 1}, false)", "blade-bench.js") is True    # results: Run again lives in the header
    assert node("m.headerRunShown(null, true)", "blade-bench.js") is True        # running: the header shows progress
