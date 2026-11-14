"""Apply a scheme's field mapping → HTML→PDF pack + step list (LLD §13.2). The official PDF is not edited."""

from pathlib import Path

MAPPINGS_DIR = Path(__file__).parent / "mappings"


def apply_mapping(mapping: dict, facts: dict) -> dict:
    raise NotImplementedError("slice 9")


def build_pack(application_id: str) -> dict:
    raise NotImplementedError("slice 9")
