import re
from typing import Any

ALLOW = "allow"
APPROVAL_REQUIRED = "approval_required"
DENY = "deny"

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_SENSITIVE_PATTERN = re.compile(
    r"\b(password|passwd|api[_ -]?key|secret|oauth token|access token|"
    r"private key|ssh key|ssn|credit card)\b",
    re.IGNORECASE,
)


def evaluate_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    customer: dict[str, Any] | None,
) -> dict[str, str]:
    """Evaluate the narrow automated-dispatch rules from POL-COM-005."""
    content = f"{subject}\n{body}"
    if _SENSITIVE_PATTERN.search(content):
        return {
            "decision": DENY,
            "reason": "Sensitive credentials or protected data must not be sent in cleartext.",
        }

    recipient = recipient.strip().lower()
    verified_contacts = []
    customer_domains: set[str] = set()
    if customer:
        for field in ("billing_contact", "technical_contact"):
            verified_contacts.extend(
                email.lower() for email in _EMAIL_PATTERN.findall(str(customer.get(field, "")))
            )
        for email in verified_contacts:
            if "@" in email:
                customer_domains.add(email.rsplit("@", 1)[1])

    if (
        recipient in verified_contacts
        and recipient.rsplit("@", 1)[1] in customer_domains
    ):
        return {
            "decision": ALLOW,
            "reason": "Recipient is a verified customer contact and the domain matches the customer record.",
        }

    return {
        "decision": APPROVAL_REQUIRED,
        "reason": (
            "The recipient is not a verified customer contact with a matching "
            "customer domain; human approval is required."
        ),
    }
