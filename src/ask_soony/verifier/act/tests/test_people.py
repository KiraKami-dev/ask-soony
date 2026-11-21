"""Roles read from (made-up) org chart and policy readings."""

from datetime import datetime

from ask_soony.verifier.act.people import People, decider, site_lead


def reading(doc_type, date, *texts, path="doc.pdf"):
    return {
        "doc_path": path,
        "attrs": {
            "doc_type": doc_type,
            "document_date": date,
            "statements": [{"topic": "other", "text_en": t} for t in texts],
        },
    }


ORG = reading(
    "org_chart",
    "2026-01-01",
    "Reporting: Ann Berg (Head of Buying) > Carl Dahl (Category Lead Widgets; Eva Falk, Buyer), "
    "TBD (Category Lead Gadgets).",
    "Site Lead DC Riverside is Gus Holm.",
    path="org_old.pdf",
)
POLICY = reading(
    "policy", "2025-01-01", "Exceptions must be documented and approved by the Head of Buying."
)
NOW = datetime(2026, 2, 1, 12)


def test_roles_names_and_vacancies():
    people = People([ORG, POLICY], NOW)
    assert (
        people.label(people.holder("category lead", "widgets"), "?")
        == "Carl Dahl, Category Lead Widgets"
    )
    assert people.holder("category lead", "gadgets").name is None  # TBD: vacant
    assert people.label(people.exception_approver(), "?") == "Ann Berg, Head of Buying"
    assert (
        people.label(site_lead(people, ["DC-RV", "Riverside"]), "?")
        == "Gus Holm, Site Lead DC Riverside"
    )


def test_vacant_category_goes_to_the_exception_approver():
    who, why = decider(People([ORG, POLICY], NOW), "Gadgets")
    assert who == "Ann Berg, Head of Buying"
    assert "Category Lead Gadgets is vacant" in why and "Head of Buying" in why
    assert decider(People([ORG, POLICY], NOW), "Widgets")[0] == "Carl Dahl, Category Lead Widgets"


def test_a_newer_chart_with_a_future_start_still_means_vacant_now():
    new = reading(
        "org_chart",
        "2026-01-20",
        "Ida Juhl is Category Lead Gadgets from 2026-02-05.",
        path="org_new.pdf",
    )
    who, why = decider(People([ORG, new, POLICY], NOW), "Gadgets")
    assert who == "Ann Berg, Head of Buying"
    assert "until Ida Juhl starts on 2026-02-05" in why
    later = datetime(2026, 2, 6, 9)
    assert (
        decider(People([ORG, new, POLICY], later), "Gadgets")[0]
        == "Ida Juhl, Category Lead Gadgets"
    )


def test_no_org_chart_names_the_role_instead_of_a_person():
    who, _ = decider(People([], NOW), "Gadgets")
    assert who == "approver of exceptions (see the approval policy)"
