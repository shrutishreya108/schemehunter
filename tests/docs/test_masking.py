import base64
import re

import pytest

from app.docs.masking import mask_fields, mask_ids, name_mismatch
from app.docs.vault import VaultKeyError, decrypt, encrypt, load_key, path_for

TWELVE_DIGITS = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}")


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("1234 5678 9012", "XXXX XXXX 9012"),
        ("123456789012", "XXXX XXXX 9012"),
        ("1234-5678-9012", "XXXX XXXX 9012"),
        ("Aadhaar: 2345 6789 0123 issued", "Aadhaar: XXXX XXXX 0123 issued"),
        ("आधार 9876 5432 1098", "आधार XXXX XXXX 1098"),
    ],
)
def test_mask_ids(raw: str, masked: str) -> None:
    assert mask_ids(raw) == masked


def test_leaves_other_numbers_alone() -> None:
    assert (
        mask_ids("IFSC SBIN0001234, PIN 560001, ph 98450 12345")
        == "IFSC SBIN0001234, PIN 560001, ph 98450 12345"
    )
    assert mask_ids("A/c 12345678901234") == "A/c 12345678901234"  # 14-digit account no.: not an Aadhaar


def test_mask_fields_leaves_no_12_digit_number_anywhere() -> None:
    extracted = {
        "name_latin": "Meena Kumari",
        "id_last4": "9012",
        "address_line": "Ward 4, ref 1234 5678 9012",
        "notes": ["1111-2222-3333", {"deep": "444455556666"}],
        "confidence": 0.93,
    }
    masked = mask_fields(extracted)
    assert not TWELVE_DIGITS.search(str(masked))
    assert masked["confidence"] == 0.93 and masked["name_latin"] == "Meena Kumari"


def test_name_mismatch() -> None:
    assert not name_mismatch(["Meena Kumari"], {"name_latin": "MEENA KUMARI"})
    assert not name_mismatch(["Meena Kumari"], {"name_latin": "Kumari Meena"})
    assert name_mismatch(["Meena Kumari"], {"name_latin": "Ravi Kumar"})
    assert not name_mismatch(["Meena Kumari"], {"name_latin": ""})


def test_vault_round_trip(tmp_path) -> None:
    key = load_key(base64.b64encode(b"k" * 32).decode())
    blob = encrypt(b"passbook bytes", key)
    assert blob[12:] != b"passbook bytes" and decrypt(blob, key) == b"passbook bytes"
    assert encrypt(b"x", key) != encrypt(b"x", key)  # fresh nonce every time
    assert path_for("c1", "d1", tmp_path) == tmp_path / "c1" / "d1.bin"


def test_vault_rejects_short_key() -> None:
    with pytest.raises(VaultKeyError):
        load_key(base64.b64encode(b"short").decode())
