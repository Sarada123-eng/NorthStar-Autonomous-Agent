# NORTHSTAR SYSTEMS INC.

## General Operations & Data Governance Policy

**Document Identifier:** POL-OPS-004  
**Classification:** Internal Confidential  
**Version:** 2.3  
**Effective Date:** November 1, 2025  
**Document Owner:** Operations & Governance Committee  

---

### 1. Purpose & Scope

This policy defines the core operational standards, data integrity guidelines, change management criteria, and task handling protocols for Northstar Systems Inc. ("Northstar"). It establishes mandatory rules for handling customer records, task dependencies, operational updates, and conflict resolution across all operational business systems.

Compliance with this policy is mandatory for all personnel, contractors, and internal business processing workflows across all functional departments.

---

### 2. Customer Record Integrity

#### 2.1 Creation of New Customer Records
A new customer record may be generated within **Customer Records** (CRM) if and only if all of the following conditions are met:
1. An executed commercial contract, formal order form, or authorized evaluation agreement exists.
2. An existing record search has been executed to verify that no active, duplicate, or historical account entity exists for the same legal business entity or tax identification number.

#### 2.2 Duplicate Detection & Pre-Creation Verification
Prior to record creation, personnel must perform duplicate detection across **Customer Records** using:
* Legal entity name and registered domain (e.g., `acme.com`).
* Corporate tax identification or registration number (where applicable).
* Primary billing contact email address.

#### 2.3 Handling Existing Records
If an existing account record is identified:
* **Parent-Subsidiary Relationships:** If the entity is a subsidiary or regional division of an existing Enterprise account, it must be created as a Child Account linked to the Primary Account entity, rather than as an independent root record.
* **Re-Activation / Expansion:** If the record represents a former customer re-engaging or an existing customer purchasing additional entitlements, the existing root account entity must be updated rather than duplicated.

#### 2.4 Mandatory Record Fields
No customer record may be transitioned to an active operational status without the following verified fields:
* Legal Account Name & Primary Operating Region (NA, EMEA, APAC).
* Primary Billing Contact (Name, Title, Verified Corporate Email, Billing Address).
* Primary Technical Contact (Name, Title, Verified Corporate Email).
* Subscribed Platform Tier (Standard, Business, Enterprise).
* Assigned Commercial Owner (Account Executive) and operational owner (Onboarding Manager during onboarding for Business and Enterprise accounts, the CSM thereafter, or Pooled CS for Standard).

#### 2.5 Authoritative Data Sources
* **Customer Records (CRM):** Authoritative source for account identity, commercial contacts, tier assignments, and account ownership.
* **Finance & Invoicing System:** Authoritative source for active billing terms, invoice delivery status, payment history, and credit standings.
* **Northstar Operations (Admin System):** Authoritative source for real-time technical tenant configuration, feature flags, and deployment metrics.

#### 2.6 Contact Verification at Onboarding
A contact named in an executed contract and recorded in Customer Records from that contract is treated as verified for onboarding purposes, provided the email address uses the Account's registered corporate domain and the contact is not flagged Stale. A contact is not verified if the address uses a different domain from the registered corporate domain, if a required contact field has no named individual or title, or if the contact appears only in an invoice, email thread or project note. Contacts from superseded agreements or earlier invoices do not carry over automatically; they are re-confirmed through the Account Executive or the customer's verified Technical Contact. Completion of a verified contact's authentication profile remains an Onboarding Complete criterion (SOP-OPS-012 §10).

---

### 3. Data Consistency & Synchronization

#### 3.1 Contract vs. Operational System Value Reconciliation
In the event of a discrepancy between a field value in **Customer Records** or **Northstar Operations** and an executed contract:
* The executed contract on file in **Customer Records** represents the legal truth for commercial entitlements.
* Operational system values must be reconciled to reflect contractually agreed limits within two (2) business days of execution.

#### 3.2 Handling Stale Information
* Customer contacts and technical configurations unverified for greater than twelve (12) months are flagged as **Stale**.
* Operational updates or support requests originating from a **Stale** account require re-verification of the Primary Technical Contact prior to executing high-privilege configuration changes or tenant modifications.

#### 3.3 Mandatory Confirmation Rules
Re-confirmation with the customer's verified Technical Contact or Billing Contact is required prior to applying any of the following operational modifications:
* Modification of primary user administrator email addresses.
* Changes to data residency region settings.
* Reductions or expansions in provisioned user seat licenses.
* Alterations to automated billing cycle schedules.

---

### 4. Task Management & Execution Standards

#### 4.1 Task Ownership
Every operational task generated within the **Project/Task System** must have a single assigned functional owner (individual employee or named role queue). Unassigned tasks are non-compliant and must be auto-assigned to the appropriate department triage queue within two (2) business hours.

#### 4.2 Required Metadata
All operational tasks must include:
* Associated **Account ID** and **Contract Identifier**.
* Functional Department (Sales, Onboarding, SE, Security, Support, Finance).
* Priority Level (Low, Medium, High, Critical / P1).
* Target Completion Date aligned with standard SLA schedules.
* Dependency linkages to prerequisite tasks.

#### 4.3 Prevention of Duplicate Tasks
Before creating a new task, personnel must check the **Project/Task System** for open tasks under the same Account ID with matching scope (e.g., "Tenant Provisioning" or "Security Review"). Duplicate tasks must be consolidated under the existing active task ticket.

#### 4.4 Task Sequencing & Prerequisite Verification
Tasks tagged with explicit dependencies must not be set to "In Progress" or "Completed" until all prerequisite dependency tasks display "Closed/Verified" status.

---

### 5. Operational Change Management

Operational modifications are categorized into three distinct tiers governing required authorization and logging:

```
+---------------------------------------------------------------------------------------+
|                               OPERATIONAL CHANGE TIERS                                |
+-----------------------+-------------------------------+-------------------------------+
|      ROUTINE          |     APPROVAL REQUIRED         |      CRITICAL / AUDITED      |
+-----------------------+-------------------------------+-------------------------------+
| • Password resets     | • Temporary SLA adjustments   | • Data schema modifications   |
| • Standard seat adds  | • Custom connector activation | • Data deletion requests      |
| • Contact updates     | • Non-standard billing terms  | • Production tenant teardown  |
| • Ticket assignments  | • Resource limit increases    | • Security policy exemptions  |
+-----------------------+-------------------------------+-------------------------------+
```

* **Routine Changes:** Can be executed directly by front-line staff; logged in standard system audit trails.
* **Approval-Required Changes:** Require explicit approval from the relevant Department Manager (e.g., Finance Manager for custom terms, Security Lead for custom integrations) prior to execution.
* **Critical / Audited Changes:** Require documented dual-signoff (Department Lead + Security/Operations Officer) and must maintain full audit retention.

---

### 6. Handling Missing or Conflicting Information

#### 6.1 Permissible Operational Defaults
When specific non-critical preferences are omitted from customer requests or setup forms, staff may apply standard company operational defaults, including:
* Standard UI language (English).
* Default platform log retention period (90 days).
* Standard automated daily backup execution windows (02:00 UTC).

#### 6.2 Mandatory Clarification (No Assumptions Permitted)
Personnel are strictly forbidden from making assumptions or using default values for the following parameters:
* **Platform Tier:** Never assume tier level; must be explicitly confirmed via contract.
* **Data Residency Region:** Never assume data hosting region; must match contract specifications or default to the customer's legal corporate address country.
* **Security & Regulatory Workload Tags:** Never assume non-regulated status if the customer's industry or contract hints at HIPAA, GDPR, or PCI data handling.

#### 6.3 Discrepancy Protocol
When conflicting instructions exist between customer communications and internal contract records:
1. Pause the impacted workflow task.
2. Flag the task as "Blocked - Information Conflict."
3. Escalate to the assigned Account Executive or CSM to request written customer clarification.

---

### 7. Auditability & Evidence Retention

* All operations affecting customer tenant status, billing parameters, user access rights, and data configurations must generate an immutable log entry in the primary operating system.
* **Retention Window:** System audit logs and commercial approvals must be retained for a minimum of seven (7) years to comply with regulatory and audit requirements.
* **Evidence Linkage:** Written customer sign-offs, security approvals, and contract addenda must be attached directly to the account profile in **Customer Records**.

---

### 8. Idempotency & Repeated Operations

Operational processes must be designed and executed idempotently. Repeated requests or re-transmissions of identical business commands (e.g., "Provision Enterprise Tenant for Account X") must recognize existing completed states and evaluate to a no-op or status check rather than generating duplicate account entities, extra billable line items, or duplicate tasks.

---

### 9. Exceptions, Escalation & Conflict Resolution

#### 9.1 Escalation Thresholds
An operational task or customer request must be escalated immediately to the Department Lead under any of the following conditions:
* Anticipated SLA breach within 4 hours.
* Total customer financial exposure or requested concession exceeds $10,000.
* Dispute regarding customer data ownership or regulatory compliance boundaries.

#### 9.2 Data Source Precedence Model
When operational guidelines, contract clauses, and security mandates interact, personnel must apply the following hierarchy of precedence:

```
  +-------------------------------------------------------------+
  |  1. MANDATORY SECURITY & COMPLIANCE REQUIREMENTS (HIGHEST)  |
  |     (SOC 2, HIPAA, GDPR, ISO 27001, Encryption Mandates)    |
  +-------------------------------------------------------------+
                                │
                                ▼
  +-------------------------------------------------------------+
  |  2. EXECUTED CUSTOMER CONTRACTUAL COMMITMENTS               |
  |     (Order Forms, MSA, Custom SLA Addenda, Signed SOWs)     |
  +-------------------------------------------------------------+
                                │
                                ▼
  +-------------------------------------------------------------+
  |  3. COMPANY OPERATIONAL DEFAULTS & STANDARD PROCEDURES      |
  |     (Standard SOPs, Default Systems Config, Templates)      |
  +-------------------------------------------------------------+
```

* **Nuance Rule:** A signed customer contract *supersedes* standard company operational defaults (e.g., a contract specifying 2-hour response times overrides the Standard 8-hour operational default). However, a customer contract *cannot override* mandatory Information Security and Legal Compliance mandates (e.g., a customer contract requesting unencrypted data storage cannot override Northstar's mandatory encryption policies).

* **Where the other documents sit:** Operational policies (this policy, POL-COM-005, POL-OPS-009) and SOPs are level 3. Within level 3, a policy prevails over an SOP where they differ, because policies set internal controls and SOPs set procedure. The Company Profile is background and prevails over nothing above it.
* **Contracts and internal controls:** A contract can change what Northstar has committed to deliver, but it does not waive Northstar's internal approval and record-integrity controls. A non-standard commitment (for example a custom response target, log retention or data-handling term) is honoured only once the approvals required by the relevant policy are obtained; until then, standard-process work proceeds and the non-standard element is escalated.
* **Controlled exceptions:** A small number of security requirements may be relaxed with documented Security Lead or CISO approval (see POL-SEC-002 §9.3). Mandatory baselines may not be.
* **Same-level conflicts:** Prefer the current version over a superseded one, the document specific to the customer over a general one, and the later effective date over the earlier. If the conflict remains unresolved, escalate under POL-OPS-009 §3.3 or §3.4.

---

### 10. Document Validity & Record Selection

#### 10.1 Identifying the Governing Contract
Customer contracts are identified by legal entity name, registration or tax identification number, registered domain, address and contract identifier. A document governs only if all of the following hold:
1. Its status is executed and active (not draft, expired, terminated or superseded).
2. Its term covers the relevant date.
3. It is not named as superseded by a later executed agreement for the same legal entity.

When more than one agreement exists for the same legal entity, the latest executed agreement governs. Earlier agreements remain historical reference for account history, prior contacts and legacy configuration only, and their contacts, domains and entitlements do not carry over unless the successor agreement says so. An agreement for one legal entity never governs another, including a parent, subsidiary or affiliate with a similar name.

#### 10.2 Entity Resolution
If a request identifies a customer by a short or partial name and exactly one active legal entity matches, work proceeds and the basis for the match is recorded. If the name matches more than one active legal entity and neither the request, the account history nor the ownership records distinguish between them, the requester (or Revenue Operations) must confirm which entity is meant before any record is created or changed.

#### 10.3 Invoice Records
Invoices are identified by invoice number, issue date and referenced contract. A revised invoice carries the original number with a revision suffix (for example -R1) and states that it supersedes the original; once a revision is issued, only the latest active revision is a valid billing document and the original is retained for audit only. Where several invoices exist for an account, the current valid invoice is the most recently issued invoice that has not been superseded. Amount and due date are taken as stated on the face of the current valid invoice. An invoice must be matched to the correct legal entity and contract before it is logged; where the billing details on the invoice (entity name, address, billing contact, contract reference) differ from the governing contract, the difference is recorded with the logged invoice and referred to Finance Operations. Logging an invoice that is already recorded updates the existing entry and does not create a duplicate (Section 8).

### 11. Document Versioning & Governance

| Version | Effective Date | Policy Owner | Summary of Main Changes |
| :--- | :--- | :--- | :--- |
| **1.0** | Jan 10, 2024 | Operations Board | Initial framework publication. |
| **2.0** | Feb 15, 2025 | Governance Comm. | Added data source precedence model and duplicate detection standards. |
| **2.3** | Nov 01, 2025 | Dir. Business Ops | Refined change management tiers, audit retention, and idempotency guidelines. |

---
*For inquiries regarding this policy, contact the Operations & Governance Committee.*
