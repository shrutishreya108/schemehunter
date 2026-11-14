"""Slice 9: project report + prefill pack + request_submit, through the tools. PDF bytes are faked where WeasyPrint's
native libraries are missing (a bare Windows box); the HTML templates are always rendered for real."""

import base64

import pytest

from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.config import get_settings
from app.forms.prefill import apply_mapping, format_value, load_mapping, missing_fields, steps_for
from app.pdf import render as pdf
from app.rules.loader import load_schemes

pytestmark = pytest.mark.db


@pytest.fixture
def vault_env(settings_db, tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    get_settings.cache_clear()
    yield tmp_path / "vault"
    get_settings.cache_clear()


@pytest.fixture
def fake_pdf(monkeypatch):
    """Real HTML, fake PDF engine: returns the rendered HTML inside a %PDF stub so the bytes are inspectable."""
    rendered = []

    def render(template, context, lang="hi"):
        html, used = pdf.render_html(template, context, lang)
        rendered.append((template, used, html))
        return b"%PDF-1.4 stub\n" + html.encode("utf-8"), used != lang

    monkeypatch.setattr(pdf, "render", render)
    monkeypatch.setattr("app.brain.tools.prefill_form.render", render)
    return rendered


def meena_ctx(conn, seeded, fake, lang="en") -> Ctx:
    return Ctx(
        case_id=seeded["meena"],
        conn=conn,
        schemes=load_schemes(),
        state_code="KA",
        language=lang,
        client=fake,
    )


def fit_application(conn, seeded, ctx) -> str:
    dispatch("update_profile", {"key": "loan_amount", "value": 300000}, ctx)
    dispatch("match_schemes", {}, ctx)
    return dispatch("create_application", {"scheme_id": "pmegp"}, ctx)["application_id"]


# ---------------------------------------------------------------- build_project_report


def test_report_uses_code_numbers_and_the_seeded_narrative_without_calling_claude(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    fit_application(conn, seeded, ctx)
    out = dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)
    assert out["totals"] == {
        "total_cost": 500000.0,
        "subsidy": 125000.0,
        "own": 25000.0,
        "loan": 350000.0,
        "emi": 7609.85,
        "break_even_units": 225.0,
        "profit_month": 35000.0,
        "dscr": 5.02,
    }
    assert (
        out["narrative_source"] == "seed" and fake_claude.calls_for("report_narrative") == []
    )  # no credit spent
    template, lang, html = fake_pdf[-1]
    assert template == "project_report" and lang == "en"
    for text in ("₹5,00,000", "₹1,25,000", "₹3,50,000", "₹7,609.85", "Meena Kumari"):
        assert text in html
    assert "Meena Kumari will run a tailoring and boutique unit" in html  # the seeded narrative
    doc = conn.execute(
        "select doc_type, check_status, mime from documents where id = %s", (out["pdf_document_id"],)
    ).fetchone()
    assert (doc["doc_type"], doc["check_status"], doc["mime"]) == ("project_report", "OK", "application/pdf")


def test_report_in_hindi_has_hindi_labels_and_narrative(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude, lang="hi")
    fit_application(conn, seeded, ctx)
    dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)
    _, lang, html = fake_pdf[-1]
    assert lang == "hi" and "प्रोजेक्ट रिपोर्ट" in html and "परियोजना लागत" in html and "मीना कुमारी सिलाई" in html


def test_report_without_a_seeded_narrative_asks_claude_once_and_stores_it(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    fit_application(conn, seeded, ctx)
    conn.execute("delete from artifacts where case_id = %s and kind = 'report_narrative'", (seeded["meena"],))
    section = {"en": "A small tailoring unit.", "hi": "एक छोटी सिलाई यूनिट।"}
    fake_claude.add(
        {
            "report_narrative": [
                {
                    "content": [
                        {
                            "tool": "report_narrative",
                            "input": {
                                "business_description": section,
                                "market": section,
                                "location": section,
                            },
                        }
                    ]
                }
            ]
        }
    )
    first = dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)
    second = dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)
    assert first["narrative_source"] == "claude" and second["narrative_source"] == "claude"
    assert len(fake_claude.calls_for("report_narrative")) == 1  # stored: the second build costs nothing
    assert (
        ctx.meter.by_model.get(get_settings().MODEL_REASON, 0) > 0
    )  # one Sonnet call (plus the cheap guard scan)


def test_a_narrative_with_a_promise_is_replaced_by_plain_facts(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    fit_application(conn, seeded, ctx)
    conn.execute("delete from artifacts where case_id = %s and kind = 'report_narrative'", (seeded["meena"],))
    bad = {"en": "We guarantee this loan will be approved.", "hi": "लोन की गारंटी है।"}
    fake_claude.add(
        {
            "report_narrative": [
                {
                    "content": [
                        {
                            "tool": "report_narrative",
                            "input": {"business_description": bad, "market": bad, "location": bad},
                        }
                    ]
                }
            ]
        }
    )
    out = dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)
    assert out["narrative_source"] == "fallback"
    assert "guarantee" not in fake_pdf[-1][2].lower()
    assert (
        conn.execute(
            "select count(*) as n from artifacts where kind = 'report_narrative' and source = 'claude'"
        ).fetchone()["n"]
        == 0
    )


def test_report_needs_a_fit_and_a_cost_sheet(seeded, conn, vault_env, fake_pdf, fake_claude) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    conn.execute("delete from matches where case_id = %s", (seeded["meena"],))
    assert (
        "match_schemes first" in dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)["error"]
    )  # no match yet
    fit_application(conn, seeded, ctx)
    conn.execute("delete from artifacts where case_id = %s and kind = 'cost_inputs'", (seeded["meena"],))
    assert "no cost sheet" in dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)["error"]
    assert "unknown scheme_id" in dispatch("build_project_report", {"scheme_id": "x"}, ctx)["error"]


def test_report_reports_a_missing_pdf_engine_cleanly(
    seeded, conn, vault_env, fake_claude, monkeypatch
) -> None:
    def unavailable(*a, **k):
        raise pdf.PdfUnavailable("no pango")

    monkeypatch.setattr(pdf, "render", unavailable)
    ctx = meena_ctx(conn, seeded, fake_claude)
    fit_application(conn, seeded, ctx)
    assert "PDF engine" in dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)["error"]


# ---------------------------------------------------------------- prefill_form, request_submit


def test_prefill_pack_fills_the_mapped_fields_and_lists_the_steps(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    app_id = fit_application(conn, seeded, ctx)
    conn.execute(
        "insert into profile_answers (case_id, key, value, source) values (%s, 'address_line', '\"12 MG Road\"', 'typed'), (%s, 'district', '\"Bengaluru Urban\"', 'typed')",
        (seeded["meena"], seeded["meena"]),
    )
    out = dispatch("prefill_form", {"application_id": app_id}, ctx)
    assert (
        out["missing_fields"] == []
        and len(out["steps"]) == 5
        and "Press Submit using the OTP" in out["steps"][3]
    )
    html = fake_pdf[-1][2]
    for text in ("Meena Kumari", "12/04/1991", "OBC", "₹5,00,000", "12 MG Road, Bengaluru Urban, Karnataka"):
        assert text in html
    assert "Copy each value" in html and "only you can do this" in html


def test_prefill_marks_missing_facts_instead_of_inventing_them(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    app_id = fit_application(conn, seeded, ctx)
    out = dispatch("prefill_form", {"application_id": app_id}, ctx)  # no address on file
    assert out["missing_fields"] == ["unit_address"]
    assert "missing: ask me to add it" in fake_pdf[-1][2]


def test_prefill_refuses_another_cases_application_and_unmapped_schemes(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    theirs = str(
        conn.execute("select id from applications where case_id = %s", (seeded["fatima"],)).fetchone()["id"]
    )
    assert "no such application" in dispatch("prefill_form", {"application_id": theirs}, ctx)["error"]
    app_id = fit_application(conn, seeded, ctx)
    conn.execute("update applications set scheme_id = 'mudra' where id = %s", (app_id,))
    assert "no form mapping for mudra" in dispatch("prefill_form", {"application_id": app_id}, ctx)["error"]


def test_request_submit_asks_the_user_and_never_submits(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    app_id = fit_application(conn, seeded, ctx)
    pack = dispatch("prefill_form", {"application_id": app_id}, ctx)["pack_document_id"]
    conn.execute("update applications set state = 'FORM_READY' where id = %s", (app_id,))
    out = dispatch("request_submit", {"application_id": app_id, "pack_document_id": pack}, ctx)
    assert out["state"] == "AWAITING_HUMAN_SUBMIT" and len(out["short_code"]) == 4
    action = conn.execute(
        "select kind, status, payload from pending_actions where application_id = %s", (app_id,)
    ).fetchone()
    assert (action["kind"], action["status"]) == ("instruct_submit", "PENDING") and action["payload"][
        "pack_document_id"
    ] == pack
    again = dispatch(
        "request_submit", {"application_id": app_id, "pack_document_id": pack}, ctx
    )  # idempotent
    assert (
        again["ok"]
        and conn.execute(
            "select count(*) as n from pending_actions where application_id = %s", (app_id,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute("select state from applications where id = %s", (app_id,)).fetchone()["state"]
        == "AWAITING_HUMAN_SUBMIT"
    )


def test_request_submit_checks_the_pack_and_the_state(seeded, conn, vault_env, fake_pdf, fake_claude) -> None:
    ctx = meena_ctx(conn, seeded, fake_claude)
    app_id = fit_application(conn, seeded, ctx)
    assert (
        "pack_document_id must be"
        in dispatch(
            "request_submit",
            {"application_id": app_id, "pack_document_id": "00000000-0000-0000-0000-000000000000"},
            ctx,
        )["error"]
    )
    pack = dispatch("prefill_form", {"application_id": app_id}, ctx)["pack_document_id"]
    assert (
        "FORM_READY first"
        in dispatch("request_submit", {"application_id": app_id, "pack_document_id": pack}, ctx)["error"]
    )


def test_schedule_tool_uses_the_demo_clock_and_checks_ownership(
    seeded, conn, fake_claude, monkeypatch
) -> None:
    monkeypatch.setenv("DEMO_CLOCK_SCALE", "1440")
    get_settings.cache_clear()
    ctx = meena_ctx(conn, seeded, fake_claude)
    app_id = fit_application(conn, seeded, ctx)
    out = dispatch("schedule", {"type": "status_check", "days_from_now": 3, "application_id": app_id}, ctx)
    row = conn.execute("select run_at - now() as d from jobs where id = %s", (out["job_id"],)).fetchone()
    assert 150 < row["d"].total_seconds() < 190  # 3 days → 3 minutes
    theirs = str(
        conn.execute("select id from applications where case_id = %s", (seeded["fatima"],)).fetchone()["id"]
    )
    assert (
        "no such application"
        in dispatch("schedule", {"type": "chase", "application_id": theirs}, ctx)["error"]
    )
    assert (
        "invalid input" in dispatch("schedule", {"type": "send_email"}, ctx)["error"]
    )  # not an allowed alarm
    get_settings.cache_clear()


# ---------------------------------------------------------------- pure mapping logic (no DB)


def test_mapping_formats() -> None:
    assert format_value("1991-04-12", {"format": "DD/MM/YYYY"}) == "12/04/1991"
    assert format_value("GEN", {"map": {"GEN": "General"}}) == "General"
    assert format_value(1200000, {"format": "inr"}) == "₹12,00,000"
    assert format_value(None, {}) is None and format_value("", {}) is None


def test_mapping_join_skips_empty_parts() -> None:
    mapping = load_mapping("pmegp")
    fields = apply_mapping(mapping, {"address_line": "12 MG Road", "district": "", "state_name": "Karnataka"})
    assert {f["key"]: f["value"] for f in fields}["unit_address"] == "12 MG Road, Karnataka"
    assert set(missing_fields(fields)) >= {"applicant_name", "date_of_birth"}
    bare = {
        f["key"]: f["value"]
        for f in apply_mapping(mapping, {"district": "Mysuru", "state_name": "Karnataka"})
    }
    assert bare["unit_address"] is None  # a district and state alone are not an address


def test_steps_fall_back_for_schemes_without_a_mapping() -> None:
    assert steps_for("pmegp", "en")[0].startswith("Open the portal")
    assert steps_for("mudra", "hi") and len(steps_for("mudra", "en")) == 2


@pytest.mark.skipif(
    not pdf.weasyprint_available(), reason="WeasyPrint native libraries (Pango) missing on this machine"
)
@pytest.mark.parametrize("lang", ["en", "hi"])
def test_real_pdf_renders_in_both_languages(lang) -> None:
    from pathlib import Path

    import yaml

    from app.finance.project_report import compute, inputs_from, render_pdf, report_context

    costs = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "seed" / "costs" / "meena.yaml").read_text(encoding="utf-8")
    )
    narrative = {k: {"en": "x", "hi": "क"} for k in ("business_description", "market", "location")}
    ctx = report_context(
        costs,
        compute(inputs_from(costs, 25, 5)),
        narrative,
        "Meena Kumari",
        {"en": "PMEGP", "hi": "पीएमईजीपी"},
    )
    data, fell_back = render_pdf(ctx, lang)
    assert data.startswith(b"%PDF") and len(data) > 2000 and fell_back is False


def test_hindi_falls_back_to_english_when_the_font_is_unusable(monkeypatch) -> None:
    monkeypatch.setattr(pdf, "font_usable", lambda: False)
    html, used = pdf.render_html(
        "prefill_pack",
        {"fields": [], "steps": [], "scheme_name": {"en": "PMEGP", "hi": "पीएमईजीपी"}, "portal_url": ""},
        "hi",
    )
    assert used == "en" and "form pack" in html
