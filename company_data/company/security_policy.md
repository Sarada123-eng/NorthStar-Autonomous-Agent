# NORTHSTAR SYSTEMS INC.

## Corporate Information Security & Data Protection Policy

**Document Identifier:** POL-SEC-002  
**Classification:** Internal Confidential  
**Version:** 3.1  
**Effective Date:** January 10, 2026  
**Document Owner:** Office of the Chief Information Security Officer (CISO) & Security Governance Committee  

---

### 1. Purpose and Scope

This Information Security & Data Protection Policy defines the mandatory security controls, data protection boundaries, authorization protocols, and risk mitigation practices for Northstar Systems Inc. ("Northstar").

This policy applies to all employees, contractors, temporary staff, and automated operational service accounts accessing Northstar systems, customer environments, internal network infrastructure, and data repositories.

---

### 2. Risk Classification Framework for Operational Actions

To establish clear authorization thresholds and governance controls, all system interactions, task executions, and operational routines are categorized into four distinct risk classifications:

```
+-----------------------------------------------------------------------------------+
|                        OPERATIONAL ACTION RISK CLASSIFICATION                     |
+-----------------------------------------------------------------------------------+
| 1. LOW-RISK OPERATIONAL ACTIONS                                                   |
|    • Internal read-only queries against CRM, task systems, or knowledge bases.     |
|    • Internal status updates, notes logging, and non-sensitive metadata tagging.  |
+-----------------------------------------------------------------------------------+
| 2. INTERNAL WRITE ACTIONS                                                         |
|    • Creating or updating standard internal task tickets, records, or assignments.|
|    • Generating internal staging tenants without external connectivity.           |
+-----------------------------------------------------------------------------------+
| 3. EXTERNAL SIDE EFFECTS                                                          |
|    • Dispatching external emails, webhook events, or customer notifications.      |
|    • Executing live production API payload calls to external endpoints.           |
+-----------------------------------------------------------------------------------+
| 4. SECURITY-SENSITIVE ACTIONS                                                     |
|    • Provisioning production database access, granting admin privileges.          |
|    • Modifying encryption parameters, IP whitelists, or data residency settings.  |
+-----------------------------------------------------------------------------------+
```

#### 2.1 Low-Risk Operational Actions
Actions that read internal status data or modify non-sensitive metadata without modifying system access controls, customer production configurations, or billing ledgers. Examples: querying ticket queues, updating internal task assignment tags, or reviewing account profile summaries.

#### 2.2 Internal Write Actions
Actions that create or modify operational state within internal business applications without directly impacting external customer environments or dispatching external transmissions. Examples: logging customer call notes, generating internal onboarding project plans, or updating account owner assignments.

#### 2.3 Actions Involving External Side Effects
Actions that transmit communications, send API payloads, or execute workflow commands across external boundaries. Examples: dispatching transactional onboarding emails to customer contacts, sending webhook alerts to customer endpoints, or issuing automated contract renewal notifications.

#### 2.4 Security-Sensitive Actions
High-privilege actions that alter environment security boundaries, modify access controls, impact data residency, adjust cryptographic settings, or export confidential records. Examples: provisioning administrative access, executing custom data export scripts, configuring firewall/IP allowlists, or issuing single sign-on (SSO) certificate changes.

---

### 3. Customer Data Protection & PII Standards

#### 3.1 Confidentiality & Isolation
Customer data ingested into or processed by the Northstar Flow Orchestrator platform is classified as **Customer Confidential**. Data must remain logically or physically isolated by tenant boundaries in accordance with contractual specifications and security baseline architecture.

#### 3.2 Personally Identifiable Information (PII)
* **Scope:** PII includes full names, national identity numbers, personal email addresses, direct phone numbers, IP addresses, and authentication credentials.
* **Storage & Transmission:** PII must be encrypted at rest using AES-256 (or higher) and in transit using TLS 1.3. PII must never be stored in unencrypted internal logs, ticket notes, or public communication channels.
* **Data Minimization:** Operational tasks must process only the minimum necessary PII required to perform the intended administrative function.

#### 3.3 Regulated Workloads (HIPAA / PHI, PCI-DSS, GDPR)
Accounts processing Protected Health Information (PHI), payment card data, or European Union personal data require explicit regulatory environment tags (`HIPAA_ENABLED`, `PCI_COMPLIANT`, `EU_DATA_RESIDENCY`) enabled in **Northstar Operations**. Unverified migration of regulated workloads to standard, non-compliant infrastructure is strictly prohibited.

---

### 4. External Communication & Information Sharing

#### 4.1 Authorized Recipients & Destination Verification
External communications containing technical configurations, system credentials, or tenant access links must be sent **exclusively** to verified customer contacts (Primary Technical Contact or Primary Billing Contact) recorded in **Customer Records**.

#### 4.2 Ambiguous or Unverified Recipients
* If an external communication or operational delivery lists a recipient email address whose domain does not match the registered corporate domain of the account in **Customer Records**, the action must be **halted immediately**.
* For non-sensitive content (for example a kickoff invitation or project schedule), the message is held for the human approval defined in POL-COM-005 §6.2. For sensitive system access details, credentials or tenant links, personnel must additionally issue an identity verification request to the Account Executive and receive documented authorization before dispatch to third-party domains (e.g., external implementation consultants).

#### 4.3 Restrictions on Exporting Customer Records
Bulk exports of customer records, billing logs, or account lists to external storage locations, unencrypted message channels, or non-Northstar platforms are prohibited without explicit authorization from the CISO or Security Lead.

#### 4.4 Continuous Log Forwarding and Customer-Side Mirroring
Forwarding or mirroring tenant execution logs, audit trails or payload metadata to a customer-controlled or third-party destination (for example a customer SIEM collector or an on-premises appliance) is a Security-Sensitive Action. It may be configured only after Security Lead approval, and only where the destination is authenticated, uses TLS 1.2 or higher with signed requests, and the Security Lead has confirmed what the stream may contain. Streams from tenants flagged `HIPAA_ENABLED` or `PCI_COMPLIANT` must not carry PHI or cardholder data unless the Security Lead has approved the safeguards in writing. Northstar's own audit logging and seven-year retention (Section 10) continue regardless of any customer-side copy.

---

### 5. Credentials, Secrets & Access Governance

#### 5.1 Prohibition of Cleartext Credentials
System passwords, API secret keys, OAuth tokens, and SSH private keys must never be written, stored, or transmitted in plain text across email, tickets, or internal documentation. Credentials must be issued via encrypted secret vault URLs with strict expiration timeouts.

#### 5.2 Least Privilege Access Control
Access permissions across all internal systems (**Northstar Operations**, **Customer Records**, **Finance System**) must follow the Principle of Least Privilege. Elevated administrative rights must be granted on a time-bound, role-justified basis and automatically revoked upon task completion.

#### 5.3 Temporary Escalation Approval
Temporary elevation of account access to perform emergency troubleshooting or custom database modifications requires a documented **Privileged Access Request** ticket approved by a Security Lead or Engineering Lead.

---

### 6. Security Review Requirements & High-Risk Configurations

#### 6.1 Mandatory Security Review Triggers
A formal **Security Review** executed by a Security Analyst or the Security Lead is required prior to executing operational tasks under any of the following conditions:

1. The account is subscribed under the **Enterprise Tier**.
2. The agreement involves custom Data Processing Addenda (DPA), HIPAA Business Associate Agreements (BAA), or localized data residency mandates.
3. The customer contract or request requires custom single-tenant infrastructure provisioning.
4. The workflow involves configuring custom raw SQL query execute nodes or execution of arbitrary customer-provided binaries within cloud runtime workers.

#### 6.2 High-Risk Customer Configurations
* **IP Whitelisting Exemption:** Requests to disable IP whitelisting or allow unrestricted `0.0.0.0/0` ingress access to a production tenant are classified as **High-Risk Exceptions** and require Security Lead sign-off.
* **Custom Certificates:** Usage of custom SSL/TLS certificates or disabling SSL certificate validation on outbound webhooks/connectors is strictly forbidden in production environments.

---

### 7. Actions Involving External Systems & Integrations

#### 7.1 Webhook & Endpoint Validation
Outbound integrations configured within Northstar Flow Orchestrator must communicate over TLS 1.2 or higher using authenticated endpoints (OAuth2, Mutual TLS, or HMAC signed headers).

#### 7.2 Third-Party Sandbox Access
When connecting customer systems during onboarding or implementation testing, operational staff must utilize designated staging or sandbox environments. Under no circumstances should test workflows be configured to write or modify data within a customer's active production endpoint without explicit customer sign-off.

---

### 8. Security Approvals, Incidents & Mandatory Escalation

#### 8.1 Required Approvals Summary Matrix

| Action Type | Risk Level | Required Approval Authority |
| :--- | :--- | :--- |
| Standard task logging / internal ticket status update | Low | Self / Standard Workflow |
| Issuing standard onboarding emails to verified contacts | External Side Effect | Standard Role Authorization |
| Provisioning Standard / Business Tier shared tenant | Internal Write | Automated / Standard OM |
| Disabling IP restriction / Modifying firewall rules | Security-Sensitive | Security Lead |
| Exporting bulk customer profile records | Security-Sensitive | CISO / Security Lead |
| Granting production database administrative access | Security-Sensitive | CISO + Engineering Lead |

#### 8.2 Missing Required Security Information
If an operational task or onboarding workflow lacks mandatory security parameters (e.g., unconfirmed encryption requirements, missing data residency selection, unverified DPA status), staff **must not proceed using generic default assumptions**. The task must be flagged as "Blocked - Pending Security Clarification" and referred to the Security Lead.

#### 8.3 Security Incident Reporting
Any observed security anomaly—including suspected credential compromise, unauthorized access attempts, inadvertent disclosure of PII, or unexpected cross-tenant data visibility—must be reported within **15 minutes** of discovery via a Priority 1 Security Incident ticket to `security-incident@northstarsystems.com`.

---

### 9. Non-Override Mandates & Precedence Rules

#### 9.1 Inviolability of Security Controls
The rules established in this policy represent **Mandatory Security Baselines**. Standard operational expediency, sales commitments, or informal customer requests cannot bypass, relax, or override these mandates.

#### 9.2 Resolution of Contractual Conflicts with Security Baselines
In the event that an executed customer contract contains a clause that directly conflicts with a mandatory security policy, **this Information Security Policy takes absolute precedence**.

* **Illustrative Real-World Example:**  
  * *Context:* An Enterprise customer executes an Order Form containing a custom clause requesting that their database backups be saved to an unencrypted public AWS S3 bucket for easy integration with their external analytics tool.
  * *Policy Precedence Application:* Regardless of the commercial agreement or Account Executive approval, Northstar operational staff are strictly prohibited from provisioning unencrypted storage. The mandatory rule enforcing AES-256 encryption at rest overrides the customer contract clause. The issue must be escalated to the Security Lead and Legal Counsel to negotiate an encrypted alternative (e.g., customer-managed KMS key access over TLS).

#### 9.3 Mandatory Baselines and Controlled Exceptions
Mandatory baselines cannot be waived by any role or by any contract. They include encryption of customer data at rest and in transit, regulatory environment tagging for regulated workloads, tenant isolation, and audit logging. Controlled exceptions are the specific items this policy allows the Security Lead (or the CISO where stated) to approve in writing, for example IP allowlist exemptions (Section 6.2), customer-side log forwarding (Section 4.4) and sharing access details with third-party domains (Section 4.2). A contract clause that falls within a controlled exception is not self-executing: the work waits for the written approval. A contract clause that conflicts with a mandatory baseline is escalated to the Security Lead and Legal Counsel to negotiate an alternative.

---

### 10. Audit Logging & Verification Requirements

#### 10.1 Immutable System Audit Trails
All security-sensitive actions, configuration changes, user provisioning events, credential issuances, and external API calls must be logged in the centralized, immutable audit log framework in **Northstar Operations**.

#### 10.2 Audit Log Contents
Every log entry must capture:
* Exact timestamp in UTC.
* Identity of the executing user account or automated service account.
* Originating IP address and session ID.
* Targeted Account ID and tenant resource.
* Specific action performed and prior/updated configuration state.

#### 10.3 Log Integrity & Retention
Audit logs are read-only and protected against modification or deletion. Logs must be retained for a minimum of seven (7) years to support regulatory compliance, SOC 2 audits, and forensic investigations.

---

### 11. Document Versioning & Governance

| Version | Effective Date | Policy Owner | Key Revisions |
| :--- | :--- | :--- | :--- |
| **2.0** | Jan 15, 2024 | Security Governance | Annual security baseline refresh. |
| **3.0** | Aug 12, 2025 | Office of the CISO | Defined Action Risk Framework (Low-Risk, Write, Side-Effect, Security-Sensitive). |
| **3.1** | Jan 10, 2026 | Governance Board | Added explicit precedence rules for contract vs. security policy conflicts and unverified domain restrictions. |

*For security guidance, incident response, or policy clarifications, contact the CISO Security Operations Team.*
