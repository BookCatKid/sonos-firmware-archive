import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_legacy_flash_prefixes.py"
SPEC = importlib.util.spec_from_file_location("test_legacy_flash_prefixes_script", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_file_prefixes_default_and_all_aligned(tmp_path):
    blocks = [bytes([value]) * MODULE.PREFIX_BYTES for value in range(4)]
    candidate = tmp_path / "flash.bin"
    candidate.write_bytes(b"".join(blocks))

    assert list(MODULE.file_prefixes(candidate)) == [
        ("first", blocks[0]),
        ("last", blocks[3]),
    ]
    assert list(MODULE.file_prefixes(candidate, all_aligned=True)) == [
        ("offset-0x0", blocks[0]),
        ("offset-0x4000", blocks[1]),
        ("offset-0x8000", blocks[2]),
        ("offset-0xc000", blocks[3]),
    ]
