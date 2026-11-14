"""Generate 20 clearly-fake specimen document images + tests/docs/fixtures/labels.yaml.

Every image carries a big diagonal "SPECIMEN / DEMO" stamp and placeholder text. They are not realistic
IDs: no real layout, no real numbers (the only digits are "XXXX XXXX 9012"-style placeholders). The labels
file also records what FakeClaude "sees" for each image (its vision output), because tests never call the
API. Re-run after editing CASES:  python scripts/make_specimen_docs.py
"""

import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "tests" / "docs" / "fixtures"
SIZE = (900, 560)
FONT = ImageFont.load_default(size=34)
SMALL = ImageFont.load_default(size=24)
BIG = ImageFont.load_default(size=88)

NAME = "Meena Kumari"
OTHER = "Ravi Kumar"


def fields(name: str, kind: str) -> dict:
    base = {
        "name_native": "",
        "name_latin": name,
        "dob": "1991-04-12",
        "address_line": "SPECIMEN ADDRESS",
        "district": "Bengaluru Urban",
        "id_last4": "9012",
        "bank_name": "",
        "ifsc": "",
    }
    if kind == "passbook":
        base.update({"bank_name": "SPECIMEN BANK", "ifsc": "SPEC0000001", "dob": None})
    return base


def model(
    detected: str,
    name: str = NAME,
    readable: bool = True,
    issues: list | None = None,
    raw_id: str | None = None,
) -> dict:
    """What the (fake) vision model returns. raw_id sneaks a full 12-digit number into a field,
    so the mask tests prove the code layer catches a model that ignores the prompt."""
    f = fields(name, detected)
    if raw_id:
        f["address_line"] = f"SPECIMEN ADDRESS, ID {raw_id}"
    return {
        "doc_type_detected": detected,
        "readable": readable,
        "quality_issues": issues or [],
        "fields": f,
        "confidence": 0.93 if readable and not issues else 0.4,
    }


# file, claimed type, how to draw, expected status, vision output
CASES = [
    ("aadhaar_clear_1", "aadhaar", "aadhaar", [], "OK", model("aadhaar", raw_id="1234 5678 9012")),
    ("aadhaar_clear_2", "aadhaar", "aadhaar", [], "OK", model("aadhaar", raw_id="123456789012")),
    ("aadhaar_clear_3", "aadhaar", "aadhaar", [], "OK", model("aadhaar", raw_id="1234-5678-9012")),
    ("aadhaar_clear_4", "aadhaar", "aadhaar", [], "OK", model("aadhaar")),
    ("aadhaar_glare", "aadhaar", "aadhaar", ["glare"], "OK", model("aadhaar", issues=["glare"])),
    ("aadhaar_blurry", "aadhaar", "aadhaar", ["blur"], "RETAKE", model("aadhaar", issues=["blur"])),
    ("aadhaar_cut_off", "aadhaar", "aadhaar", ["crop"], "RETAKE", model("aadhaar", issues=["cut_off"])),
    ("aadhaar_too_dark", "aadhaar", "aadhaar", ["dark"], "RETAKE", model("aadhaar", issues=["too_dark"])),
    (
        "aadhaar_unreadable",
        "aadhaar",
        "aadhaar",
        ["blur", "blur"],
        "RETAKE",
        model("aadhaar", readable=False),
    ),
    ("aadhaar_is_actually_pan", "aadhaar", "pan", [], "PROBLEM", model("pan")),
    ("aadhaar_is_actually_passbook", "aadhaar", "passbook", [], "PROBLEM", model("passbook")),
    ("passbook_clear_1", "passbook", "passbook", [], "OK", model("passbook")),
    ("passbook_clear_2", "passbook", "passbook", [], "OK", model("passbook")),
    ("passbook_blurry", "passbook", "passbook", ["blur"], "RETAKE", model("passbook", issues=["blur"])),
    ("passbook_name_mismatch", "passbook", "passbook", [], "PROBLEM", model("passbook", name=OTHER)),
    ("passbook_is_actually_aadhaar", "passbook", "aadhaar", [], "PROBLEM", model("aadhaar")),
    ("photo_clear", "photo", "photo", [], "OK", model("photo")),
    ("quotation_clear", "quotation", "quotation", [], "OK", model("quotation")),
    ("caste_cert_clear", "caste_cert", "caste_cert", [], "OK", model("caste_cert")),
    ("passbook_cut_off", "passbook", "passbook", ["crop"], "RETAKE", model("passbook", issues=["cut_off"])),
]
assert len(CASES) == 20


def draw_card(kind: str, serial: int) -> Image.Image:
    img = Image.new("RGB", SIZE, (245, 245, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, SIZE[0] - 10, SIZE[1] - 10], outline=(60, 60, 60), width=4)
    d.text((40, 30), f"{kind.upper()} (SPECIMEN)", fill=(20, 20, 20), font=FONT)
    d.text(
        (620, 40), f"SAMPLE #{serial:02d}", fill=(90, 90, 90), font=SMALL
    )  # keeps every image's bytes unique
    for i, line in enumerate(
        ["NAME: SPECIMEN PERSON", "DOB: 01/01/2000", "ID: XXXX XXXX 9012", "NOT A REAL DOCUMENT"]
    ):
        d.text((40, 120 + i * 55), line, fill=(40, 40, 40), font=SMALL)
    stamp = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((60, 220), "SPECIMEN / DEMO", fill=(200, 0, 0, 150), font=BIG)
    img.paste(stamp.rotate(20, resample=Image.BICUBIC), (0, 0), stamp.rotate(20, resample=Image.BICUBIC))
    return img


def degrade(img: Image.Image, effects: list[str]) -> Image.Image:
    for e in effects:
        if e == "blur":
            img = img.filter(ImageFilter.GaussianBlur(9))
        elif e == "crop":
            w, h = img.size
            img = img.crop((0, 0, int(w * 0.62), int(h * 0.7)))
        elif e == "dark":
            img = ImageEnhance.Brightness(img).enhance(0.18)
        elif e == "glare":
            glare = Image.new("RGB", img.size, (255, 255, 255))
            mask = Image.radial_gradient("L").resize(img.size).point(lambda v: 255 - v)
            img = Image.composite(glare, img, mask.point(lambda v: v // 2))
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = []
    for n, (name, claimed, drawn, effects, expect, vision) in enumerate(CASES, 1):
        degrade(draw_card(drawn, n), effects).save(OUT / f"{name}.jpg", quality=90)
        labels.append({"file": f"{name}.jpg", "claimed": claimed, "expect": expect, "vision": vision})
    (OUT / "labels.yaml").write_text(
        "# Generated by scripts/make_specimen_docs.py. `vision` = what FakeClaude returns for the image.\n"
        + yaml.safe_dump(labels, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote {len(labels)} specimen images + labels.yaml to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
