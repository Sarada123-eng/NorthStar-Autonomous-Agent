# NORTHSTAR SYSTEMS INC.

## Corporate Customer Communication & External Transmission Policy

**Document Identifier:** POL-COM-005

**Classification:** Internal Confidential

**Version:** 3.0

**Effective Date:** February 1, 2026

**Document Owner:** Customer Experience & Communications Governance Committee

### 1. Purpose & Scope

This policy governs all outbound customer-facing and external communications dispatched by Northstar Systems Inc. ("Northstar"). It establishes contextual, argument-sensitive standards for evaluating communication risk based on recipient verification status, message content sensitivity, and contractual authorization.

This policy applies to all electronic mail, automated platform notifications, transactional alerts, and operational updates dispatched through the **Communication / Email System** across all operational lifecycle stages.

### 2. Communication Classification Framework

Northstar categorizes operational transmissions into two distinct operational scopes:

```
+-----------------------------------------------------------------------------------+
|                        COMMUNICATION BOUNDARY CLASSIFICATION                      |
+-----------------------------------------------------------------------------------+
| 1. INTERNAL COMMUNICATIONS                                                        |
|    • Messages routed exclusively to @northstarsystems.com domain addresses.        |
|    • In-app task notifications within internal systems (CRM, Project System).     |
|    • Operational Queue Risk Level: LOW / UNRESTRICTED                             |
+-----------------------------------------------------------------------------------+
| 2. EXTERNAL COMMUNICATIONS                                                        |
|    • Messages routed to external customer domains or third-party addresses.       |
|    • Automated webhook payloads sent to customer-hosted endpoints.               |
|    • Operational Queue Risk Level: VARIABLE / ARGUMENT-SENSITIVE                  |
+-----------------------------------------------------------------------------------+

```

### 3. Approved Customer Communication Types

External communications must align with one of the following recognized operational categories:

1. **Transactional Onboarding Communications:** System credentials, sandbox URLs, kickoff scheduling, administrative enablement guidance.

2. **Operational & Service Alerts:** Maintenance windows, scheduled platform upgrades, service degradation alerts, SLA status reports.

3. **Billing & Commercial Notices:** Recurring invoice dispatches, payment receipts, tax form requests, delinquent balance reminders.

4. **Support Ticket Communications:** Ticket updates, technical troubleshooting queries, resolution sign-off requests.

5. **Security & Regulatory Notices:** Mandatory security advisory notices, DPA updates, incident disclosures.

*Unsolicited commercial marketing, bulk promotional campaigns, or unverified operational announcements fall outside the scope of this policy and must be managed via Marketing Communications SOPs.*

### 4. Customer Contact Verification & Recipient Rules

#### 4.1 Primary Verified Contacts

An external recipient is classified as a **Verified Customer Contact** if their full name, corporate title, direct email address, and role tag (Technical Contact or Billing Contact) exist within **Customer Records** attached to an active Account ID. A named executive or Business Contact who is not also tagged Technical or Billing is not a Verified Customer Contact, and messages to such a recipient follow the approval route in Section 6.2. Contacts recorded from an executed contract are verified as described in POL-OPS-004 §2.6.

#### 4.2 Corporate Domain Matching Rule

External email addresses must strictly match the primary corporate domain registered in **Customer Records** (e.g., `user@acme.com` matching Account Domain `acme.com`).

#### 4.3 Rules for CC and BCC Fields

* **CC (Carbon Copy):** Permitted only if all added CC email addresses are verified contacts under the same Account ID in **Customer Records**, or represent assigned internal Northstar account owners (`@northstarsystems.com`).

* **BCC (Blind Carbon Copy):** External email addresses are **strictly forbidden** in the BCC field. BCC is reserved exclusively for internal compliance archiving mailboxes (`compliance-archive@northstarsystems.com`).

### 5. Content Sensitivity & Contractual Data Rules

#### 5.1 Permissible Information in Standard Emails

* General onboarding project schedules and milestone target dates.

* Standard administrative login URLs (non-secret).

* Ticket references, standard system documentation links, and Knowledge Repository references.

* Standard invoice PDF attachments containing public billing terms.

#### 5.2 Sensitive Information Guidelines

The following items are classified as **Sensitive Transmission Items**:

* Temporary initial user passwords, SSO setup tokens, or API secret keys.

* System architectural diagrams or internal vulnerability review notes.

* Customer PII arrays or unencrypted dataset previews.

* Custom pricing addenda, non-standard discount schedules, or dispute settlement terms.

*Rules for Sensitive Transmission:* Sensitive items must **never** be sent in cleartext body text. System access keys and initial credentials must be issued via self-destructing, encrypted Secret Vault links with 24-hour expiration limits.

### 6. Context-Sensitive Decision Rules (Automated vs. Approval Required vs. Blocked)

Transmissions are evaluated dynamically using a three-tier risk model based on recipient context, content classification, and account status:

```
                  +-----------------------------------+
                  | Outbound Communication Evaluated  |
                  +-----------------------------------+
                                    │
                                    ▼
                 [ Evaluate Recipient & Content Context ]
                                    │
         +--------------------------+--------------------------+
         |                          |                          |
         v                          v                          v
+------------------+       +------------------+       +------------------+
| AUTOMATED SEND   |       | HUMAN APPROVAL   |       | MANDATORY BLOCK  |
| (LOW RISK)       |       | (MEDIUM RISK)    |       | (HIGH RISK)      |
+------------------+       +------------------+       +------------------+
• Verified Contact         • Third-Party Domain       • Unencrypted Secrets
• Standard Category        • Custom SOW / Terms       • Stale/Deactivated Contact
• Matching Domain          • Non-Standard SLA         • Bulk Contact Export

```

#### 6.1 Situations Permitting Automated Dispatch (Low Risk)

A communication may be dispatched automatically without prior human review **only when ALL of the following criteria are satisfied**:

1. **Verified Recipient:** The destination address matches a verified Technical or Billing Contact in **Customer Records**.

2. **Domain Match:** The recipient email domain matches the verified account domain.

3. **Standard Category:** The message uses an approved, standardized template (e.g., Standard Onboarding Welcome, Routine Ticket Update, System Maintenance Alert).

4. **No Sensitive Cleartext:** The message contains no cleartext credentials, unencrypted PII, or custom financial concessions.

#### 6.2 Situations Requiring Explicit Human Approval (Medium Risk)

A communication must be held in the **Communication / Email System** draft queue pending explicit human sign-off from the assigned Onboarding Manager, CSM, or Department Lead under any of the following conditions:

1. **Third-Party Domain Recipient:** The email is addressed to an external consultant or implementation partner whose email domain differs from the customer's primary account domain, even if listed in project notes. *(Requires CSM Approval)*.

2. **Custom Contract Terms:** The email contains custom SLA commitments, modified milestone schedules, or customized Statement of Work (SOW) deliverables not present in standard templates. *(Requires OM/CSM Approval)*.

3. **Billing Discrepancy / Credit Memo Notices:** Communications involving invoice adjustments or fee waivers exceeding $1,000. *(Requires Finance Operations Approval)*.

4. **Non-Standard CC Recipients:** The message includes external CC recipients who are not explicitly tagged as primary Billing or Technical contacts in Customer Records. *(Requires OM Approval)*.

5. **Business Contact Recipient:** The email is addressed to a named executive or Business Contact who is not tagged as Technical or Billing contact in Customer Records, but whose email domain matches the account's registered domain. *(Requires OM/CSM Approval)*.

#### 6.3 Situations Where Transmission Must Be Blocked (High Risk / Non-Negotiable)

A communication must be **immediately blocked** and an alert logged in the Project & Task System under any of the following conditions:

1. **Cleartext Password/Secret Transmission:** The message contains raw passwords, private API keys, or unencrypted database credentials in the email body or standard attachments.

2. **Unverified / Stale Recipient:** The recipient contact is marked as "Inactive", "Deactivated", or "Stale" (>12 months unverified) in Customer Records.

3. **Regulated Data Exposure:** The email contains unencrypted Protected Health Information (PHI), payment card numbers, or sensitive GDPR PII datasets.

4. **External BCC Usage:** An external email address is detected in the BCC field.

5. **Conflicting Account Status:** Attempting to send operational system access credentials to an Account flagged as "Terminated", "Suspended - Delinquent", or "Legal Hold".

### 7. Mandatory Onboarding Communication Content

All formal transactional **Onboarding Welcome Messages** dispatched to new customers must include the following verified structural elements:

1. Formal Account Legal Name and assigned Northstar **Account ID**.

2. Subscribed Platform Tier designation (Standard, Business, Enterprise).

3. Encrypted Secret Vault Link for administrative credential retrieval (or SSO setup guide).

4. Verified contact details for the assigned operational owner (Pooled CS for Standard; Named OM/CSM for Business and Enterprise).

5. Official link to the Northstar Customer Support Portal and Knowledge Repository.

6. Contractual Target Go-Live Date (as recorded in the executed agreement).

### 8. Message Correction & Resend Protocols

If an incorrect, incomplete, or misdirected email is dispatched:

1. **Recall & Halt:** Immediately halt any scheduled follow-up automated queue messages linked to the thread.

2. **Correction Notice Execution:**

   * If the error was technical/minor (e.g., broken documentation link), issue a corrected follow-up message referencing the original ticket ID within 2 hours.

   * If the error involved misdirected customer information or incorrect commercial terms, issue a formal **Operational Escalation** to the Support Lead and CISO Security Team within 15 minutes.

3. **Audit Annotation:** Log a correction entry in **Customer Records** explaining the cause of the resend and linking the original and corrected message IDs.

### 9. Auditability & Evidence Requirements

* **System Log Archival:** Every outbound external communication must generate an immutable log entry in the **Communication / Email System** capturing: UTC timestamp, sender identity, recipient email address(es), associated Account ID, template ID, and complete message body headers.

* **Retention Period:** Outbound communication logs and approval authorization receipts must be retained for a minimum of seven (7) years to maintain SOC 2 Type II audit compliance.

### 10. Document Versioning & Governance

| Version | Effective Date | Policy Owner | Key Revisions |
| ----- | ----- | ----- | ----- |
| **1.0** | Jan 20, 2024 | Communications Board | Initial communication policy rollout. |
| **2.0** | Jul 11, 2025 | Customer Experience | Introduced domain matching rules and mandatory CC/BCC controls. |
| **3.0** | Feb 01, 2026 | Governance Committee | Added argument-sensitive dynamic decision matrix (Automated, Approval, Blocked). |

*For questions regarding external communication authorization or template verification, contact the Customer Experience & Communications Governance Committee.*

