from drlf.reporting.first_pass import (
    MISSING_EVIDENCE_CHECKLIST,
    ROLE_AND_MONEY_CAVEATS,
)


def test_first_pass_guardrails_cover_operational_and_payment_roles() -> None:
    caveats = " ".join(ROLE_AND_MONEY_CAVEATS).casefold()
    checklist = " ".join(MISSING_EVIDENCE_CHECKLIST).casefold()

    for role in ("prescriber", "dispenser", "administrator", "billing", "payment recipient"):
        assert role in caveats
    assert "not prescriber revenue" in caveats
    assert "suppression" in caveats
    assert "telehealth" in checklist
    assert "census" in checklist
    assert "remittance" in checklist
