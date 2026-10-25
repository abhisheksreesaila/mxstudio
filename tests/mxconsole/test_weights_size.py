"""F-062 review: the Size column's number is kept while a folder is unchanged, grows as a pull adds files, ignores broken links
and gives up (None) on a tree too big to be a weights folder."""
import os

from mxconsole import removal


_tick = [1_700_000_000_000_000_000]


def put(folder, name, size):
    (folder / name).write_bytes(b"x" * size)
    # a directory's mtime moves when a file lands in it; make each landing visibly later, whatever the clock's grain
    _tick[0] += 1_000_000_000
    os.utime(folder, ns=(_tick[0], _tick[0]))


def test_a_pull_still_adding_files_is_not_stuck_at_its_first_size(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    put(snap, "a.safetensors", 1000)
    assert removal.weights_size(str(snap)) == 1000
    put(snap, "b.safetensors", 500)
    assert removal.weights_size(str(snap)) == 1500


def test_an_unchanged_folder_is_not_walked_again(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    snap.mkdir()
    put(snap, "a.bin", 10)
    assert removal.weights_size(str(snap)) == 10
    monkeypatch.setattr("pathlib.Path.rglob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("walked again")))
    assert removal.weights_size(str(snap)) == 10


def test_a_broken_link_is_not_counted_and_does_not_break_the_total(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    put(snap, "a.bin", 40)
    (snap / "dangling").symlink_to(tmp_path / "nowhere")
    assert removal.weights_size(str(snap)) == 40


def test_a_single_weights_file_and_a_missing_or_empty_path(tmp_path):
    f = tmp_path / "model.gguf"
    f.write_bytes(b"y" * 77)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert [removal.weights_size(str(f)), removal.weights_size(str(empty)), removal.weights_size(str(tmp_path / "gone")), removal.weights_size(None)] == [77, None, None, None]


def test_a_tree_with_too_many_files_shows_no_size_instead_of_slowing_the_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(removal, "MAX_SIZE_FILES", 5)
    big = tmp_path / "project"
    big.mkdir()
    for i in range(6):
        put(big, f"f{i}", 1)
    ok = tmp_path / "fine"
    ok.mkdir()
    for i in range(5):
        put(ok, f"f{i}", 1)
    assert removal.weights_size(str(big)) is None
    assert removal.weights_size(str(ok)) == 5
