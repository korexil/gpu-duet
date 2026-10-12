"""The ctypes binding and the C bridge must agree on every field, not only on struct sizes.

The library part runs when GPU_DUET_LIBRARY points at a built qwen3_tts_bridge
(.dll/.so); CI builds the CPU-only bridge on Linux for it.
"""
import ctypes as C
import os
from pathlib import Path

import pytest

from native_engine import ABI_STRUCTS, Params, check_abi, expected_layout, expected_types, token_budget


def test_type_codes_cover_every_field_and_tell_same_width_types_apart():
    codes = expected_types()
    assert len(codes) == sum(len(s._fields_) for s in ABI_STRUCTS)
    names = [name for s in ABI_STRUCTS for name, _ in s._fields_]
    by_name = dict(zip(names, codes))
    assert by_name['max_audio_tokens'] == 1 and by_name['temperature'] == 3   # both 4 bytes, different codes
    assert by_name['seed'] == 2 and by_name['instruction'] == 4 and by_name['generation'] == 5


def test_layout_lists_every_field_in_order():
    layout = expected_layout()
    assert len(layout) == sum(1 + len(s._fields_) for s in ABI_STRUCTS)
    index = 0
    for struct in ABI_STRUCTS:
        assert layout[index] == C.sizeof(struct)
        offsets = layout[index + 1:index + 1 + len(struct._fields_)]
        assert offsets == sorted(offsets) and offsets[0] == 0
        index += 1 + len(struct._fields_)


def test_the_local_abi_extension_sits_at_the_end_of_params():
    names = [name for name, _ in Params._fields_]
    assert names[-2:] == ['seed', 'full_text_input']


def test_token_budget_scales_with_text_and_stays_bounded():
    assert token_budget('うん') == 128
    assert token_budget('x' * 40) == 320
    assert token_budget('x' * 300) == 1024


@pytest.mark.skipif(not os.environ.get('GPU_DUET_LIBRARY'), reason='set GPU_DUET_LIBRARY to a built bridge')
def test_built_library_matches_the_binding():
    library = Path(os.environ['GPU_DUET_LIBRARY']).resolve()  # add_dll_directory needs an absolute path
    if hasattr(os, 'add_dll_directory'):
        for folder in (library.parent, library.parent / 'bin'):
            if folder.is_dir():
                os.add_dll_directory(str(folder))
    assert check_abi(C.CDLL(str(library))) == 'layout+types'
