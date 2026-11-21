"""
Who holds which role, read from the documents: the newest org chart and the approval policy.

Nothing here names a person or a role holder. Patterns find 'Name (Role)', 'Name, Role',
'Name is Role from DATE', 'Role is Name' and 'site lead Name' in what the reader extracted, so a
new org chart (a role filled, a role vacated) changes the routing with no code change.
"""

import re
from dataclasses import dataclass
from datetime import datetime

NAME = r"(?:Dr\. )?[A-Z][a-z]+(?: [A-Z][a-z]+)+"
PATTERNS = [
    # 'Carl Dahl (Category Lead Widgets; ...)', 'TBD (Category Lead ...)'
    (re.compile(rf"({NAME}|TBD) \(([^;)]+)"), "name_role"),
    # 'Eva Falk, Buyer Widgets'
    (re.compile(rf"({NAME}), ([A-Z][^,;)]+)"), "name_role"),
    # 'Ida Juhl is Category Lead Gadgets from 2026-02-05'
    (
        re.compile(rf"({NAME}) is ([A-Z][^.(]+?)(?: from (\d{{4}}-\d{{2}}-\d{{2}}))?(?: \(|\.|$)"),
        "name_is_role",
    ),
    # 'Site Lead DC Riverside is Gus Holm'
    (
        re.compile(rf"([A-Z][\w&' ]*?(?:Lead|Head|Manager|Officer|Planner)[\w&' ]*?) is ({NAME})"),
        "role_is_name",
    ),
    # 'DC-RV Riverside: site lead Gus Holm'
    (re.compile(rf"([\w-]+(?: [A-Z]\w+)?): (site lead) ({NAME})"), "site_lead"),
]


ROLE_WORD = re.compile(r"\b(?:Lead|Buyer|Manager|Officer|Planner|Head)\b")


@dataclass
class Holder:
    name: str | None  # None: the role is vacant
    role: str
    starts: str | None  # ISO date the person takes the role, if stated
    source: str  # document it was read from
    doc_date: str | None


def _roles_in(text: str) -> list[tuple[str, str, str | None]]:
    """(name, role, starts) found in one statement."""
    found = []
    for pattern, shape in PATTERNS:
        for match in pattern.finditer(text):
            if shape == "name_role":
                found.append((match.group(1), match.group(2).strip(), None))
            elif shape == "name_is_role":
                found.append((match.group(1), match.group(2).strip(), match.group(3)))
            elif shape == "role_is_name":
                found.append((match.group(2), match.group(1).strip(), None))
            elif shape == "site_lead":
                found.append((match.group(3), f"Site Lead {match.group(1)}", None))
    return found


class People:
    """Role holders from org charts and policies; the newest document wins for a role."""

    def __init__(self, readings: list[dict], now: datetime | None):
        self.now = now
        self.holders: list[Holder] = []
        self.policy: list[str] = []
        self.policy_name = "the approval policy"
        docs = [r for r in readings if r["attrs"].get("doc_type") in ("org_chart", "policy")]
        for reading in sorted(docs, key=lambda r: r["attrs"].get("document_date") or ""):
            for statement in reading["attrs"].get("statements") or []:
                text = statement.get("text_en") or ""
                if reading["attrs"].get("doc_type") == "policy":
                    self.policy.append(text)
                    self.policy_name = reading["doc_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                for name, role, starts in _roles_in(text):
                    if "(" in role or ROLE_WORD.search(name):  # half-matches like 'Senior Buyer'
                        continue
                    self.holders.append(
                        Holder(
                            None if name == "TBD" else name,
                            role,
                            starts,
                            reading["doc_path"],
                            reading["attrs"].get("document_date"),
                        )
                    )

    def holder(self, *words: str) -> Holder | None:
        """The newest holder of a role whose name contains all `words` (case-insensitive)."""
        wanted = [w.lower() for w in words if w]
        matches = [h for h in self.holders if all(w in h.role.lower() for w in wanted)]
        return max(matches, key=lambda h: (h.doc_date or "", h.starts or ""), default=None)

    def active(self, holder: Holder | None) -> bool:
        if holder is None or holder.name is None:
            return False
        if holder.starts and self.now and holder.starts > self.now.date().isoformat():
            return False
        return True

    def exception_approver(self) -> Holder | None:
        """Whoever the policy says approves exceptions ('Exceptions must be ... approved by X')."""
        for text in self.policy:
            match = re.search(r"[Ee]xceptions?[^.]*approved by (?:the )?([A-Z][\w&' ]+?)\.", text)
            if match:
                return self.holder(*match.group(1).split())
        return None

    def label(self, holder: Holder | None, fallback: str) -> str:
        if holder is None or holder.name is None:
            return fallback
        return f"{holder.name}, {holder.role}"


def site_lead(people: People, site_names: list[str]) -> Holder | None:
    """The site lead of a site, matched by any of its names (id, city, full name)."""
    for name in site_names:
        for word in (name or "").replace("-", " ").split():
            found = people.holder("site lead", word)
            if found and found.name:
                return found
    return None


def decider(people: People, category: str | None) -> tuple[str, str]:
    """
    Who decides on a part of `category`: its Category Lead; if that role is vacant now, whoever
    approves exceptions. Returns (who, why).
    """
    lead = people.holder("category lead", *(category or "").split()[:1]) if category else None
    if people.active(lead):
        return people.label(lead, "Category Lead"), f"Category Lead for {category}"
    escalate = people.exception_approver()
    why = f"Category Lead {category} is vacant"
    if lead and lead.name and lead.starts:
        why += f" until {lead.name} starts on {lead.starts}"
    why += f"; per {people.policy_name} exceptions go to the " + (
        escalate.role if escalate else "next level"
    )
    return people.label(escalate, f"approver of exceptions (see {people.policy_name})"), why
