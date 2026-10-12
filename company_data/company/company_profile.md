# NORTHSTAR SYSTEMS INC.
## Internal Operational Reference & Company Profile
**Document Identifier:** REF-ORG-001  
**Classification:** Internal Confidential  
**Version:** 4.2  
**Effective Date:** January 15, 2026  
**Document Owner:** Operations & Governance Committee  

---

### 1. Company Overview

#### 1.1 Mission & Product Offering
Northstar Systems Inc. ("Northstar") is a mid-market B2B Software-as-a-Service (SaaS) provider specializing in cloud-native business workflow automation. Our flagship platform, **Northstar Flow Orchestrator**, enables mid-market and enterprise organizations to model, automate, and monitor complex cross-departmental business processes across disparate legacy software systems, cloud applications, and database environments.

#### 1.2 Company Scale
Northstar currently employs approximately 320 full-time equivalent (FTE) employees distributed across remote and office-based locations. Headcount is primary distributed across core functions in Engineering, Product, Revenue (Sales & Customer Success), Customer Experience, Security, and Corporate Operations.

#### 1.3 Business Model
Northstar operates on an annual contract value (ACV) subscription-based business model. Contracts are typically executed on multi-year or annual terms, with options for annual or quarterly invoicing. Platform monetization is driven by:
* Core platform tier access fees.
* Managed workflow connector volume licenses.
* Active transaction/execution volume allowances.
* Professional Services engagements (Professional Services Statements of Work for custom implementations, training, and integrations).

#### 1.4 Target Customer Profile
Our primary customer base consists of mid-market firms (200 to 5,000 employees) and select enterprise divisions within major corporations across regulated and high-compliance industries, including Financial Services, Healthcare Administration, Logistics, and Higher Education.

#### 1.5 Operating Regions & Compliance Jurisdictions
Northstar actively sells and supports platform deployments in three core geographic operating regions:
* **North America (NA):** United States, Canada. Primary data centers located in US-East and US-West regions.
* **Europe, Middle East, and Africa (EMEA):** European Union and United Kingdom. Data hosting options compliant with EU GDPR and UK GDPR.
* **Asia-Pacific (APAC):** Regional headquarters in Singapore supporting Australia, New Zealand, and select Southeast Asian hubs.

---

### 2. Organizational Structure & Departmental Functions

#### 2.1 Sales (Revenue Operations)
Responsible for net-new customer acquisition, contract renewals, account expansions, and commercial negotiation. Sales manages the commercial lifecycle from lead qualification through final agreement execution, at which point account ownership undergoes structural handover to Customer Success.

#### 2.2 Customer Success & Professional Services
Responsible for post-sale account retention, long-term health, implementation, user adoption, and expansion identification. Customer Success collaborates directly with technical teams during onboarding and serves as the primary commercial and operational relationship manager for established accounts.

#### 2.3 Engineering & Product Development
Builds, maintains, and secures the core Northstar platform infrastructure and proprietary workflow engines. Engineering handles high-severity platform defects, core feature requests, infrastructure maintenance, and system uptime commitments.

#### 2.4 Information Security & Compliance
Maintains company security posture, manages regulatory audit processes (e.g., SOC 2 Type II, ISO 27001, HIPAA compliance framework), performs customer vendor security reviews, oversees access controls, and enforces data handling policies across all environments.

#### 2.5 Finance & Revenue Operations
Manages billing, revenue recognition, collection workflows, invoicing schedules, tax compliance, vendor accounts, and commercial credit evaluations. Finance enforces contract payment terms and manages financial holds or delinquency escalation paths.

#### 2.6 Customer Support
Provides multi-channel technical assistance, incident response, and fault resolution for active customers. Support handles standard operational tickets, operational troubleshooting, and coordinates technical escalations to Engineering.

#### 2.7 Business Operations & Governance
Coordinates cross-departmental alignment, maintains internal knowledge frameworks, oversees business application infrastructure, and ensures company-wide policy adherence.

---

### 3. Key Operational Roles & Functional Responsibilities

To ensure clear account ownership across customer lifecycles, Northstar defines specific primary roles responsible for key workflows:

* **Account Executive (AE):** The commercial owner during the pre-sale and renewal sales cycles. The AE owns contract negotiations, pricing proposals, discounts, and initial agreement signatures.
* **Customer Success Manager (CSM):** The strategic post-sale operational owner assigned to Business and Enterprise accounts. The CSM oversees long-term customer outcomes, account risk monitoring, health scoring, and renewal retention strategy.
* **Onboarding Manager (OM):** A specialized deployment role responsible for guiding customers through the initial setup, environment configuration, integration mapping, and go-live readiness phases.
* **Solutions Engineer (SE):** A technical subject matter expert who supports pre-sale technical validation, architecture reviews, proof-of-concept building, and complex solution design during implementation.
* **Security Lead (Security Analyst / CISO Delegate):** Responsible for evaluating customer-requested security addenda, evaluating third-party risks, conducting internal security reviews, and managing data processing agreements (DPAs).
* **Finance Operations Specialist:** Coordinates invoice issuance, processes custom billing requests, monitors accounts receivable status, and processes platform tier change requests within the financial ledger.
* **Support Lead (Tier 2/3 Support):** Handles high-complexity technical tickets, manages real-time SLA breach risks, coordinates incident bridging with Engineering, and oversees technical escalation channels.

---

### 4. Internal Business Terminology

Employees and internal operational platforms utilize standardized terminology. Key terms are defined as follows:

* **Account:** The umbrella entity representing a single business entity, enterprise subsidiary, or customer organization within Northstar systems. An Account encompasses all associated deployments, contracts, contacts, and billing profiles.
* **Customer:** An external business entity that maintains an active, fully executed software subscription or professional services agreement with Northstar.
* **Enterprise Customer:** An Account designated under the Enterprise customer tier, possessing custom contract terms, dedicated operational resources, or specialized SLA requirements.
* **Implementation:** The structured technical workflow required to connect customer systems, configure platform environments, build initial workflows, and validate data flows prior to full operational production use.
* **Onboarding:** The broader organizational enablement process encompassing user provisioning, technical implementation, administrative training, and change management.
* **Kickoff:** The formal initial alignment meeting between the customer team and the assigned Northstar Onboarding and CSM teams following commercial execution.
* **Service Level Agreement (SLA):** Contractually binding response time, resolution target, and platform uptime commitments extended to customers based on their tier or executed agreement.
* **Go-Live:** The formal milestone date marking when a customer transitions their workflow configurations from a sandbox or staging environment into primary operational production.
* **Billing Contact:** The verified individual designated by the customer to receive formal invoices, payment notifications, and commercial tax document requests.
* **Technical Contact:** The verified individual authorized by the customer to request platform configuration changes, submit high-priority technical support tickets, and manage platform administrator permissions.
* **Business Contact:** The customer's commercial or executive point of contact named in the contract (often the signatory). A Business Contact is not a Verified Customer Contact for automated communications unless the same person is also recorded as the Technical Contact or Billing Contact.
* **Verified Customer Contact:** A Billing Contact or Technical Contact recorded in Customer Records against an active Account, whose email domain matches the Account's registered corporate domain and who is not flagged Stale. Contacts named in an executed contract and recorded from it are treated as verified for onboarding purposes (see POL-OPS-004 §2.6).
* **Operational Owner:** The single named internal person accountable for an Account at a given time: the Account Executive before signature, the Onboarding Manager during onboarding for Business and Enterprise Accounts, and the CSM after Onboarding Complete (Pooled Customer Success for Standard).

---

### 5. Customer Tiers & Operational Distinctions

Northstar structures customer support, resource allocations, and operational handling according to three primary tiers. While detailed execution steps are governed by corresponding Service Level Agreements and Standard Operating Procedures, the overarching operational boundaries are defined below:

```
+-----------------------------------------------------------------------------------+
|                                NORTHSTAR CUSTOMER TIERS                           |
+--------------------------+--------------------------+-----------------------------+
|        STANDARD          |         BUSINESS         |         ENTERPRISE          |
+--------------------------+--------------------------+-----------------------------+
| • Standard SLA           | • Priority SLA           | • Custom/Mission-Critical   |
| • Shared Support Pool    | • Named CSM Assigned     |   SLA                       |
| • Standard Onboarding    | • Structured Onboarding  | • Dedicated CSM & Technical |
| • Standard Cloud Infra   | • Enhanced Support Hours |   Lead                      |
|                          |                          | • Dedicated/Isolated        |
|                          |                          |   Infrastructure Options    |
+--------------------------+--------------------------+-----------------------------+
```

#### 5.1 Standard Tier
* **Target Segment:** Small-to-midsize deployments operating standard business workflows with non-critical time sensitivity.
* **Support Channel:** Pooled, ticket-based electronic support during local business hours.
* **Account Ownership:** Shared Customer Success pool; no dedicated individual CSM assigned.
* **Onboarding Model:** Self-serve and automated orientation modules supported by pooled implementation guidance.
* **Deployment Architecture:** Multi-tenant shared infrastructure environment.

#### 5.2 Business Tier
* **Target Segment:** Growing mid-market organizations where workflow automation directly impacts day-to-day revenue or core internal operations.
* **Support Channel:** Priority electronic support with extended business hour coverage and escalated initial response targets.
* **Account Ownership:** Assigned named Customer Success Manager (CSM) for business review cadences and retention monitoring.
* **Onboarding Model:** Managed Onboarding program led by an assigned Onboarding Manager (OM) over a standard 30-to-45-day schedule.
* **Deployment Architecture:** Multi-tenant standard isolated logical tenant environment.

#### 5.3 Enterprise Tier
* **Target Segment:** Large enterprises or highly regulated entities with custom workflow architecture, complex security needs, and mission-critical deployment standards.
* **Support Channel:** 24/7/365 priority multi-channel support (phone, portal, dedicated messaging channels) with stringent SLA penalties.
* **Account Ownership:** Dedicated CSM and dedicated Account Executive oversight, paired with assigned Solutions Engineering hours.
* **Onboarding Model:** Custom Professional Services Statement of Work (SOW) executed with a dedicated Onboarding Manager and Solutions Engineer.
* **Deployment Architecture:** Options for multi-tenant priority isolation, single-tenant dedicated cloud hosting, or regional residency lock.

---

### 6. General Operating Principles

All personnel and operational management systems must observe the following core operating principles across all tasks and workflows:

#### 6.1 Customer Data Protection & Privacy First
Customer data stored within or processed by Northstar Flow Orchestrator is treated as strictly confidential. No employee or internal operational process may access, transfer, or modify customer workflow data without explicit, logged authorization connected to an active support ticket, security audit, or explicit customer instruction.

#### 6.2 Formal Approval Authority
Operational actions that alter commercial terms, waive standard contractual fees, grant elevated platform access, or modify customer data schemas require explicit approval from designated department authority holders. Verbal or unverified informal requests must never be executed without documented confirmation in system records.

#### 6.3 Clear Operational Ownership
Every customer ticket, account, task, and escalation must have a single designated internal primary owner. Unassigned items must be triaged immediately according to department triage protocols. Ownership transfer must be explicitly logged in the corresponding system of record.

#### 6.4 Escalation Pathways
When an issue exceeds standard SLA response thresholds, involves multi-departmental disagreement, or risks commercial churn or legal liability, it must be formally escalated to the appropriate departmental lead (e.g., Support Lead, Security Lead, Finance Operations Manager) using established ticket escalation tags.

#### 6.5 Mandatory System Auditability
All customer-facing and financial operations must leave a clear, verifiable audit trail. Actions taken across customer records, contract adjustments, provisioning changes, and security exceptions must be recorded with timestamp, identity, and context references in internal tools.

#### 6.6 Verification of Missing Context
Personnel and automated workflows must **never make assumptions** regarding incomplete or ambiguous customer information. If a critical piece of data—such as billing contact verification, SLA classification, tier assignment, or region—is missing from a request, explicit clarification must be retrieved from primary internal records or confirmed with the customer prior to execution.

---

### 7. Internal System Landscape

Operational tasks rely on a standardized ecosystem of internal software systems. Systems interact according to designated authoritative boundaries:

* **Northstar Operations (Internal Admin Platform):** The administrative backbone used to manage active software tenant deployments, feature flags, system provisioning, user authorization levels, operational health monitoring, and system license keys.
* **Knowledge Repository:** The centralized internal reference repository housing company SOPs, SLA definitions, security policies, escalation matrixes, and integration guidebooks.
* **Customer Records (CRM & Account Management):** The authoritative platform for account hierarchies, customer contact lists, sales opportunity history, active subscription statuses, CSM assignments, and customer communication logs.
* **Project & Task System:** The operational project management platform used by Onboarding, Engineering, and Professional Services to track implementation tasks, engineering defect tickets, feature requests, and customer onboarding milestones.
* **Finance & Invoicing System:** The core accounting ledger used to manage customer billing schedules, recurring invoice generation, accounts receivable tracking, credit memos, and tax documentation.
* **Communication & Email System:** The official transactional and direct email framework used to issue platform notifications, legal notices, billing alerts, and customer support correspondence.

---

### 8. Governance, Precedence & Operational Caveats

#### 8.1 Background Operational Context
The details contained within this Company Profile provide general operational context regarding Northstar Systems' standard operating structure, customer tiering, and departmental responsibilities. It serves as an authoritative overview, but does not replace explicit tactical procedures.

#### 8.2 Contractual Precedence
Where a fully executed, currently effective customer contract (e.g., Master Services Agreement, Custom SLA Addendum, or Statement of Work) conflicts with the general background in this profile, tier defaults, or standard operating procedures, the contract's explicit terms govern that customer's commercial entitlements and service commitments. A contract does not override mandatory security, legal or compliance requirements (Section 8.3), and it does not waive Northstar's internal approval and record-integrity controls: a non-standard commitment still requires the approvals defined in the governing policies. Superseded or expired agreements are historical reference only. The full order of precedence is set out in POL-OPS-004 §9.2.

#### 8.3 Inviolability of Security & Compliance Mandates
Notwithstanding customer requests or commercial considerations, mandatory Information Security guidelines, legal regulatory mandates (e.g., GDPR, HIPAA), and internal Compliance framework rules **cannot be bypassed**. Where POL-SEC-002 designates a requirement as a controlled exception, it may be relaxed only with formal written authorization from the Security Lead or Chief Information Security Officer (CISO); mandatory baselines (for example, encryption of customer data) cannot be waived by any role. Standard commercial exceptions granted by Sales or Customer Success do not apply to security policies.

#### 8.4 Policy Reference Obligation
For specific procedural execution—such as precise ticket SLA timings, custom discount threshold approvals, credit memo processing, data deletion workflows, or disaster recovery steps—personnel must consult the governing Standard Operating Procedure (SOP) documents within the Knowledge Repository.

---
**End of Document**  
*For questions regarding this document, contact the Operations & Governance Committee.*
