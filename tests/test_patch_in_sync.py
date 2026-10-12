"""The xiaozhi patch embeds the provider and wakeGate; it must match the repository files."""
from conftest import load_module

sync_patch = load_module("sync_patch", "server/sync_patch.py")


def test_embedded_files_match_the_repository():
    assert sync_patch.synced_patch() == sync_patch.current_patch()
