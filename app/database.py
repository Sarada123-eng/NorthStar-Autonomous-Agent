import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import NullPool, make_url, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

load_dotenv()

SMOKE_TEST_MODE = os.getenv("NORTHSTAR_SMOKE_TEST_MODE") == "1"
SMOKE_TEST_READ_ONLY = os.getenv("NORTHSTAR_SMOKE_TEST_READ_ONLY") == "1"
if SMOKE_TEST_MODE:
    DATABASE_URL = os.getenv("SMOKE_TEST_DATABASE_URL")
    if not DATABASE_URL:
        raise RuntimeError(
            "NORTHSTAR_SMOKE_TEST_MODE requires SMOKE_TEST_DATABASE_URL; "
            "refusing to fall back to DATABASE_URL."
        )
else:
    DATABASE_URL = os.environ["DATABASE_URL"]
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

DATABASE_NAME = make_url(DATABASE_URL).database
if SMOKE_TEST_MODE and DATABASE_NAME != "northstar_smoke":
    raise RuntimeError(
        "NORTHSTAR_SMOKE_TEST_MODE requires the database named "
        f"'northstar_smoke', got {DATABASE_NAME!r}."
    )

engine: AsyncEngine = create_async_engine(DATABASE_URL, poolclass=NullPool)


async def verify_database_target() -> str:
    """Confirm the connected database before smoke initialization or execution."""
    async with engine.connect() as connection:
        connected_name = (await connection.execute(text("SELECT current_database()"))).scalar_one()
    if SMOKE_TEST_MODE and connected_name != "northstar_smoke":
        raise RuntimeError(
            "Smoke database identity check failed: expected 'northstar_smoke', "
            f"connected to {connected_name!r}."
        )
    if SMOKE_TEST_MODE and connected_name == "northstar":
        raise RuntimeError("Refusing to run smoke mode against the development database.")
    return str(connected_name)


def _read_only_result(operation: str) -> dict[str, Any] | None:
    if SMOKE_TEST_READ_ONLY:
        return {
            "success": False,
            "error_type": "smoke_read_only",
            "retryable": False,
            "message": f"{operation} is blocked while NORTHSTAR_SMOKE_TEST_READ_ONLY=1.",
        }
    return None


def _normalize_name(name: str) -> str:
    """Normalize a customer name for matching.

    Strips leading/trailing whitespace and insignificant trailing punctuation
    (periods, commas) then collapses internal whitespace.  The result is
    lower-cased so Python-side comparisons are case-insensitive.

    This must mirror the SQL-side normalisation applied in queries:
        LOWER(TRIM(REGEXP_REPLACE(name, '[.]+$', '')))
    """
    cleaned = name.strip()
    cleaned = re.sub(r'[.]+$', '', cleaned)  # strip trailing periods
    cleaned = re.sub(r'\s+', ' ', cleaned)   # collapse whitespace
    return cleaned.casefold()


# SQL fragment that normalizes a column the same way _normalize_name does in
# Python.  Applied to both the column being compared and the parameter value.
_SQL_NORM = "LOWER(TRIM(REGEXP_REPLACE({col}, '[.]+$', '')))"


def _customer_match_clause(param: str = "name") -> str:
    """Return a SQL WHERE clause that matches customer name or legal_name
    against a normalized parameter."""
    col_name = _SQL_NORM.format(col="c.name" if param != "name" else "name")
    col_legal = _SQL_NORM.format(col="c.legal_name" if param != "name" else "legal_name")
    norm_param = _SQL_NORM.format(col=f":{param}")
    return f"{col_name} = {norm_param} OR {col_legal} = {norm_param}"


CUSTOMERS = (
    {
        "name": "Acme",
        "legal_name": "Acme Corporation Inc.",
        "billing_contact": "Daniel Okafor <ap-invoicing@acmecorp.com>",
        "technical_contact": (
            "Marcus Brody <m.brody@acmecorp.com>; "
            "Sarada <sarada161123@gmail.com>"
        ),
        "status": "onboarding",
    },
    {
        "name": "Globex",
        "legal_name": "Globex Corporation LLC",
        "billing_contact": "Billing Team <billing@globexcorp.com>",
        "technical_contact": "Technical Operations <tech@globexcorp.com>",
        "status": "active",
    },
    {
        "name": "Delta Health",
        "legal_name": "Delta Health",
        "billing_contact": "Accounts Payable <ap@deltahealth.com>",
        "technical_contact": "IT Operations <it@deltahealth.com>",
        "status": "onboarding",
    },
)

ACME_INVOICES = (
    {
        "customer_name": "Acme Corporation Inc.",
        "invoice_number": "INV-2026-1018",
        "amount": "125000.00",
        "due_date": "2026-10-31",
        "issued_date": "2026-10-01",
        "revision_number": 0,
        "status": "superseded",
        "supersedes_invoice_number": None,
    },
    {
        "customer_name": "Acme Corporation Inc.",
        "invoice_number": "INV-2026-1018-R1",
        "amount": "112500.00",
        "due_date": "2026-11-03",
        "issued_date": "2026-10-04",
        "revision_number": 1,
        "status": "active",
        "supersedes_invoice_number": "INV-2026-1018",
    },
)


async def initialize_database() -> None:
    if SMOKE_TEST_READ_ONLY:
        raise RuntimeError("Cannot initialize a database in smoke read-only mode.")
    await verify_database_target()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    legal_name TEXT,
                    billing_contact TEXT NOT NULL,
                    technical_contact TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    invoice_number TEXT NOT NULL,
                    amount NUMERIC(14, 2) NOT NULL,
                    due_date DATE NOT NULL,
                    issued_date DATE NOT NULL,
                    revision_number INTEGER NOT NULL DEFAULT 0,
                    supersedes_invoice_id INTEGER REFERENCES invoices(id),
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (customer_id, invoice_number)
                )
                """
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE customers ADD COLUMN IF NOT EXISTS legal_name TEXT"
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE (customer_id, name)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE (project_id, title)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS emails (
                    id SERIAL PRIMARY KEY,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        for customer in CUSTOMERS:
            await connection.execute(
                text(
                    """
                    INSERT INTO customers
                        (name, legal_name, billing_contact, technical_contact, status)
                    VALUES
                        (:name, :legal_name, :billing_contact, :technical_contact, :status)
                    ON CONFLICT (name) DO UPDATE SET
                        legal_name = EXCLUDED.legal_name,
                        billing_contact = EXCLUDED.billing_contact,
                        technical_contact = EXCLUDED.technical_contact,
                        status = EXCLUDED.status
                    """
                ),
                customer,
            )
        for invoice in ACME_INVOICES:
            customer = (
                await connection.execute(
                    text(
                        """
                        SELECT id FROM customers
                        WHERE name ILIKE :customer_name OR legal_name ILIKE :customer_name
                        LIMIT 1
                        """
                    ),
                    {"customer_name": invoice["customer_name"]},
                )
            ).mappings().one()
            supersedes_invoice_id = None
            if invoice["supersedes_invoice_number"]:
                superseded = (
                    await connection.execute(
                        text(
                            """
                            SELECT id FROM invoices
                            WHERE customer_id = :customer_id AND invoice_number = :invoice_number
                            """
                        ),
                        {
                            "customer_id": customer["id"],
                            "invoice_number": invoice["supersedes_invoice_number"],
                        },
                    )
                ).mappings().one()
                supersedes_invoice_id = superseded["id"]
            await connection.execute(
                text(
                    """
                    INSERT INTO invoices (
                        customer_id, invoice_number, amount, due_date, issued_date,
                        revision_number, supersedes_invoice_id, status
                    ) VALUES (
                        :customer_id, :invoice_number, :amount, :due_date, :issued_date,
                        :revision_number, :supersedes_invoice_id, :status
                    )
                    ON CONFLICT (customer_id, invoice_number) DO UPDATE SET
                        amount = EXCLUDED.amount,
                        due_date = EXCLUDED.due_date,
                        issued_date = EXCLUDED.issued_date,
                        revision_number = EXCLUDED.revision_number,
                        supersedes_invoice_id = EXCLUDED.supersedes_invoice_id,
                        status = EXCLUDED.status,
                        updated_at = NOW()
                    """
                ),
                {
                    "customer_id": customer["id"],
                    "invoice_number": invoice["invoice_number"],
                    "amount": invoice["amount"],
                    "due_date": date.fromisoformat(invoice["due_date"]),
                    "issued_date": date.fromisoformat(invoice["issued_date"]),
                    "revision_number": invoice["revision_number"],
                    "supersedes_invoice_id": supersedes_invoice_id,
                    "status": invoice["status"],
                },
            )


_LEGAL_FORM_SYNONYMS = {"incorporated": "inc", "limited": "ltd"}
_LEGAL_FORMS = {"inc", "llc", "ltd", "plc"}


def _canonical_name(name: str) -> tuple[str, str | None]:
    """Split a customer name into a comparable base and its legal-form suffix.

    "Acme Corp" -> ("acme corporation", None)
    "Acme Corporation Inc." -> ("acme corporation", "inc")
    """
    words = re.sub(r"[^\w\s]", " ", name.casefold()).split()
    words = ["corporation" if word == "corp" else word for word in words]
    words = [_LEGAL_FORM_SYNONYMS.get(word, word) for word in words]
    suffix = None
    if len(words) > 1 and words[-1] in _LEGAL_FORMS:
        suffix = words.pop()
    return " ".join(words), suffix


def _loose_customer_matches(
    name: str, customers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Match an abbreviated or suffix-less name against customer names.

    A legal-form suffix the caller did type must match, so "Acme Corporation
    Ltd" never matches "Acme Corporation Inc.".
    """
    base, suffix = _canonical_name(name)
    matches: list[dict[str, Any]] = []
    for customer in customers:
        for candidate in (customer.get("legal_name"), customer.get("name")):
            if not candidate:
                continue
            candidate_base, candidate_suffix = _canonical_name(str(candidate))
            if candidate_base == base and (suffix is None or suffix == candidate_suffix):
                matches.append(customer)
                break
    return matches


async def find_customer(name: str) -> dict[str, Any]:
    norm = _SQL_NORM.format(col="name")
    norm_legal = _SQL_NORM.format(col="legal_name")
    norm_param = _SQL_NORM.format(col=":name")
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                f"""
                SELECT id, name, legal_name, billing_contact, technical_contact, status
                FROM customers
                WHERE {norm} = {norm_param} OR {norm_legal} = {norm_param}
                ORDER BY id
                LIMIT 1
                """
            ),
            {"name": name.strip()},
        )
        row = result.mappings().first()

    if row is None:
        async with engine.connect() as connection:
            all_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT id, name, legal_name, billing_contact, technical_contact, status
                        FROM customers ORDER BY id
                        """
                    )
                )
            ).mappings().all()
        matches = _loose_customer_matches(name, [dict(item) for item in all_rows])
        if len(matches) == 1:
            return {
                "found": True,
                "resolution": {
                    "status": "resolved",
                    "reference": name.strip(),
                    "matched_on": "legal name, ignoring abbreviation or legal-form suffix",
                },
                "customer": matches[0],
            }
        if len(matches) > 1:
            return {
                "found": True,
                "resolution": {
                    "status": "ambiguous",
                    "reference": name.strip(),
                    "candidates": [item["legal_name"] or item["name"] for item in matches],
                },
                "customer": matches[0],
            }
        return {"found": False, "customer": None, "message": f"No customer found for '{name}'."}
    customer = dict(row)
    # The input matched the short display name but NOT the legal name after
    # normalisation ⇒ the user may have meant a different legal entity that
    # shares the short name.  Flag as ambiguous.
    input_norm = _normalize_name(name)
    name_norm = _normalize_name(str(customer["name"]))
    legal_norm = _normalize_name(str(customer.get("legal_name") or ""))
    if (
        customer.get("legal_name")
        and input_norm == name_norm
        and input_norm != legal_norm
    ):
        return {
            "found": True,
            "resolution": {
                "status": "ambiguous",
                "reference": name.strip(),
                "candidates": [customer["legal_name"]],
            },
            "customer": customer,
        }
    return {
        "found": True,
        "resolution": {
            "status": "resolved",
            "reference": name.strip(),
        },
        "customer": customer,
    }


async def find_customer_by_contact(recipient: str) -> dict[str, Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT id, name, legal_name, billing_contact, technical_contact, status
                FROM customers
                WHERE POSITION(LOWER(:recipient) IN LOWER(billing_contact)) > 0
                   OR POSITION(LOWER(:recipient) IN LOWER(technical_contact)) > 0
                ORDER BY id
                LIMIT 1
                """
            ),
            {"recipient": recipient.strip()},
        )
        row = result.mappings().first()

    if row is None:
        return {"found": False, "customer": None}
    return {"found": True, "customer": dict(row)}


async def create_customer(
    name: str,
    billing_contact: str,
    technical_contact: str,
) -> dict[str, Any]:
    if blocked := _read_only_result("create_customer"):
        return blocked
    customer = {
        "name": name.strip(),
        "legal_name": name.strip(),
        "billing_contact": billing_contact.strip(),
        "technical_contact": technical_contact.strip(),
        "status": "onboarding",
    }

    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO customers
                        (name, legal_name, billing_contact, technical_contact, status)
                    VALUES
                        (:name, :legal_name, :billing_contact, :technical_contact, :status)
                    RETURNING id, name, legal_name, billing_contact, technical_contact, status
                    """
                ),
                customer,
            )
            row = result.mappings().one()
    except IntegrityError as error:
        if "customers_name_key" not in str(error.orig):
            raise
        return {
            "success": False,
            "error_type": "duplicate",
            "retryable": False,
            "message": f"Customer '{customer['name']}' already exists.",
        }

    return {"success": True, "customer": dict(row)}


async def list_projects(customer_name: str) -> dict[str, Any]:
    norm_name = _SQL_NORM.format(col="c.name")
    norm_legal = _SQL_NORM.format(col="c.legal_name")
    norm_param = _SQL_NORM.format(col=":name")
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                f"""
                SELECT p.id, p.customer_id, p.name, p.status
                FROM projects p
                JOIN customers c ON c.id = p.customer_id
                WHERE {norm_name} = {norm_param} OR {norm_legal} = {norm_param}
                ORDER BY p.id
                """
            ),
            {"name": customer_name.strip()},
        )
        projects = [dict(row) for row in result.mappings().all()]
    return {"success": True, "projects": projects}


async def create_project(customer_name: str, name: str) -> dict[str, Any]:
    if blocked := _read_only_result("create_project"):
        return blocked
    norm_col = _SQL_NORM.format(col="name")
    norm_legal = _SQL_NORM.format(col="legal_name")
    norm_param = _SQL_NORM.format(col=":name")
    async with engine.begin() as connection:
        customer = (
            await connection.execute(
                text(
                    f"""
                    SELECT id FROM customers
                    WHERE {norm_col} = {norm_param} OR {norm_legal} = {norm_param}
                    LIMIT 1
                    """
                ),
                {"name": customer_name.strip()},
            )
        ).mappings().first()
        if customer is None:
            return {
                "success": False,
                "error_type": "not_found",
                "retryable": False,
                "message": f"No customer found for '{customer_name}'.",
            }

        try:
            row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO projects (customer_id, name, status)
                        VALUES (:customer_id, :name, 'onboarding')
                        RETURNING id, customer_id, name, status
                        """
                    ),
                    {"customer_id": customer["id"], "name": name.strip()},
                )
            ).mappings().one()
        except IntegrityError as error:
            if "projects_customer_id_name_key" not in str(error.orig):
                raise
            return {
                "success": False,
                "error_type": "duplicate",
                "retryable": False,
                "message": f"Project '{name}' already exists for '{customer_name}'.",
            }
    return {"success": True, "project": dict(row)}


async def list_tasks(project_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT id, project_id, title, status
                FROM tasks
                WHERE project_id = :project_id
                ORDER BY id
                """
            ),
            {"project_id": project_id},
        )
        tasks = [dict(row) for row in result.mappings().all()]
    return {"success": True, "tasks": tasks}


async def create_task(project_id: int, title: str) -> dict[str, Any]:
    if blocked := _read_only_result("create_task"):
        return blocked
    async with engine.begin() as connection:
        project = (
            await connection.execute(
                text("SELECT id FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            )
        ).mappings().first()
        if project is None:
            return {
                "success": False,
                "error_type": "invalid_project_id",
                "error": {
                    "type": "foreign_key_violation",
                    "message": f"Project ID {project_id} does not exist.",
                },
                "retryable": False,
                "message": (
                    f"Project ID {project_id} does not exist. "
                    "Use list_projects to obtain a valid project_id."
                ),
            }
        try:
            row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO tasks (project_id, title, status)
                        VALUES (:project_id, :title, 'open')
                        RETURNING id, project_id, title, status
                        """
                    ),
                    {"project_id": project_id, "title": title.strip()},
                )
            ).mappings().one()
        except IntegrityError as error:
            if "tasks_project_id_title_key" not in str(error.orig):
                raise
            return {
                "success": False,
                "error_type": "duplicate",
                "retryable": False,
                "message": f"Task '{title}' already exists in project {project_id}.",
            }
    return {"success": True, "task": dict(row)}


async def list_invoices(customer_name: str) -> dict[str, Any]:
    norm_name = _SQL_NORM.format(col="name")
    norm_legal = _SQL_NORM.format(col="legal_name")
    norm_param = _SQL_NORM.format(col=":customer_name")
    async with engine.connect() as connection:
        customer = (
            await connection.execute(
                text(
                    f"""
                    SELECT id, name, legal_name
                    FROM customers
                    WHERE {norm_name} = {norm_param} OR {norm_legal} = {norm_param}
                    LIMIT 1
                    """
                ),
                {"customer_name": customer_name.strip()},
            )
        ).mappings().first()
        if customer is None:
            return {
                "success": False,
                "error_type": "not_found",
                "retryable": False,
                "message": f"No customer found for '{customer_name}'.",
            }
        rows = await connection.execute(
            text(
                """
                SELECT i.id, i.customer_id, i.invoice_number, i.amount::TEXT AS amount,
                       i.due_date::TEXT AS due_date, i.issued_date::TEXT AS issued_date,
                       i.revision_number, i.supersedes_invoice_id,
                       prior.invoice_number AS supersedes_invoice_number,
                       i.status, i.created_at, i.updated_at
                FROM invoices i
                LEFT JOIN invoices prior ON prior.id = i.supersedes_invoice_id
                WHERE i.customer_id = :customer_id
                ORDER BY (i.status = 'active') DESC, i.issued_date DESC,
                         i.revision_number DESC, i.id DESC
                """
            ),
            {"customer_id": customer["id"]},
        )
        invoices = [dict(row) for row in rows.mappings().all()]
    active_invoice = next((invoice for invoice in invoices if invoice["status"] == "active"), None)
    return {
        "success": True,
        "customer": dict(customer),
        "invoices": invoices,
        "current_invoice": active_invoice,
    }


async def get_invoice(invoice_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT i.id, i.customer_id, c.legal_name AS customer_name,
                           c.name AS customer_short_name, i.invoice_number,
                           i.amount::TEXT AS amount, i.due_date::TEXT AS due_date,
                           i.issued_date::TEXT AS issued_date, i.revision_number,
                           i.supersedes_invoice_id,
                           prior.invoice_number AS supersedes_invoice_number,
                           i.status, i.created_at, i.updated_at
                    FROM invoices i
                    JOIN customers c ON c.id = i.customer_id
                    LEFT JOIN invoices prior ON prior.id = i.supersedes_invoice_id
                    WHERE i.id = :invoice_id
                    """
                ),
                {"invoice_id": invoice_id},
            )
        ).mappings().first()
    if row is None:
        return {
            "success": False,
            "error_type": "not_found",
            "retryable": False,
            "message": f"No invoice found for id {invoice_id}.",
        }
    invoice = dict(row)
    return {
        "success": True,
        "invoice": invoice,
        "invoice_id": invoice["id"],
        "customer_id": invoice["customer_id"],
    }


async def log_invoice(
    customer_name: str,
    invoice_number: str,
    amount: str | float,
    due_date: str,
    issued_date: str,
    revision_number: int = 0,
    status: str = "active",
    supersedes_invoice_number: str | None = None,
) -> dict[str, Any]:
    if blocked := _read_only_result("log_invoice"):
        return blocked
    try:
        normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        normalized_due_date = date.fromisoformat(due_date)
        normalized_issued_date = date.fromisoformat(issued_date)
    except (InvalidOperation, ValueError):
        return {
            "success": False,
            "error_type": "validation",
            "retryable": False,
            "message": "Amount and dates must use a decimal amount and ISO YYYY-MM-DD dates.",
        }
    if not invoice_number.strip() or revision_number < 0 or status not in {"active", "superseded"}:
        return {
            "success": False,
            "error_type": "validation",
            "retryable": False,
            "message": "Invoice number, non-negative revision number, and active/superseded status are required.",
        }

    norm_name = _SQL_NORM.format(col="name")
    norm_legal = _SQL_NORM.format(col="legal_name")
    norm_param = _SQL_NORM.format(col=":customer_name")
    async with engine.begin() as connection:
        customer = (
            await connection.execute(
                text(
                    f"""
                    SELECT id, name, legal_name
                    FROM customers
                    WHERE {norm_name} = {norm_param} OR {norm_legal} = {norm_param}
                    LIMIT 1
                    """
                ),
                {"customer_name": customer_name.strip()},
            )
        ).mappings().first()
        if customer is None:
            return {
                "success": False,
                "error_type": "not_found",
                "retryable": False,
                "message": f"No customer found for '{customer_name}'.",
            }

        supersedes_invoice_id = None
        if supersedes_invoice_number:
            prior = (
                await connection.execute(
                    text(
                        """
                        SELECT id FROM invoices
                        WHERE customer_id = :customer_id AND invoice_number = :invoice_number
                        """
                    ),
                    {
                        "customer_id": customer["id"],
                        "invoice_number": supersedes_invoice_number.strip(),
                    },
                )
            ).mappings().first()
            if prior is None:
                return {
                    "success": False,
                    "error_type": "invalid_superseded_invoice",
                    "retryable": False,
                    "message": (
                        f"Invoice '{supersedes_invoice_number}' does not exist for "
                        f"'{customer_name}'."
                    ),
                }
            supersedes_invoice_id = prior["id"]
            if status == "active":
                await connection.execute(
                    text("UPDATE invoices SET status = 'superseded', updated_at = NOW() WHERE id = :id"),
                    {"id": supersedes_invoice_id},
                )

        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO invoices (
                        customer_id, invoice_number, amount, due_date, issued_date,
                        revision_number, supersedes_invoice_id, status
                    ) VALUES (
                        :customer_id, :invoice_number, :amount, :due_date, :issued_date,
                        :revision_number, :supersedes_invoice_id, :status
                    )
                    ON CONFLICT (customer_id, invoice_number) DO UPDATE SET
                        amount = EXCLUDED.amount,
                        due_date = EXCLUDED.due_date,
                        issued_date = EXCLUDED.issued_date,
                        revision_number = EXCLUDED.revision_number,
                        supersedes_invoice_id = EXCLUDED.supersedes_invoice_id,
                        status = EXCLUDED.status,
                        updated_at = NOW()
                    RETURNING id, customer_id, invoice_number, amount::TEXT AS amount,
                              due_date::TEXT AS due_date, issued_date::TEXT AS issued_date,
                              revision_number, supersedes_invoice_id, status,
                              created_at, updated_at
                    """
                ),
                {
                    "customer_id": customer["id"],
                    "invoice_number": invoice_number.strip(),
                    "amount": normalized_amount,
                    "due_date": normalized_due_date,
                    "issued_date": normalized_issued_date,
                    "revision_number": revision_number,
                    "supersedes_invoice_id": supersedes_invoice_id,
                    "status": status,
                },
            )
        ).mappings().one()
    invoice = dict(row)
    return {
        "success": True,
        "invoice": invoice,
        "invoice_id": invoice["id"],
        "customer_id": invoice["customer_id"],
    }


async def create_email(
    recipient: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    if blocked := _read_only_result("send_email"):
        return blocked
    if not recipient.strip():
        return {
            "success": False,
            "error_type": "validation",
            "retryable": False,
            "message": "Recipient address is required.",
        }
    if not subject.strip():
        return {
            "success": False,
            "error_type": "validation",
            "retryable": False,
            "message": "Email subject is required.",
        }
    if not body.strip():
        return {
            "success": False,
            "error_type": "validation",
            "retryable": False,
            "message": "Email body is required.",
        }

    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO emails (recipient, subject, body, status)
                    VALUES (:recipient, :subject, :body, 'sent')
                    RETURNING id, recipient, subject, body, status, created_at
                    """
                ),
                {
                    "recipient": recipient.strip(),
                    "subject": subject.strip(),
                    "body": body.strip(),
                },
            )
        ).mappings().one()
    email = dict(row)
    return {
        "success": True,
        "email": email,
        "email_id": email["id"],
        "status": email["status"],
        "to": email["recipient"],
        "subject": email["subject"],
    }


async def get_email(email_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id, recipient, subject, body, status, created_at
                    FROM emails
                    WHERE id = :email_id
                    """
                ),
                {"email_id": email_id},
            )
        ).mappings().first()
    if row is None:
        return {
            "success": False,
            "error_type": "not_found",
            "retryable": False,
            "message": f"No email found for id {email_id}.",
        }
    return {"success": True, "email": dict(row)}


async def close_database() -> None:
    await engine.dispose()


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        await initialize_database()
        await close_database()

    asyncio.run(main())
