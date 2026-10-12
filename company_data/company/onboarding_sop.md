# NORTHSTAR SYSTEMS INC.

## Standard Operating Procedure: Customer Onboarding & Deployment Lifecycle

**Document Identifier:** SOP-OPS-012  
**Classification:** Internal Confidential  
**Version:** 3.4  
**Effective Date:** February 1, 2026  
**Document Owner:** Customer Operations & Professional Services  

---

### 1. Purpose and Scope

This Standard Operating Procedure (SOP) governs the end-to-end operational transition of newly contracted or upgraded accounts from the Sales lifecycle into active operational production on the Northstar Flow Orchestrator platform. 

The scope of this procedure applies to all Customer Success Managers (CSMs), Onboarding Managers (OMs), Solutions Engineers (SEs), Security Analysts, and Finance Operations Specialists involved in post-sale customer delivery.

---

### 2. Objectives & Onboarding Operational Definitions

#### 2.1 Core Objectives
* Ensure verified, audit-compliant system provisioning aligned with customer contracts.
* Minimize Time-to-Value (TTV) while mitigating security, technical, and commercial risk.
* Validate technical workflow execution against baseline contract performance expectations.

#### 2.2 Operational Milestone Definitions

##### "Ready for Onboarding"
An account is classified as **Ready for Onboarding** only when:
1. A fully executed commercial agreement (Order Form, Master Services Agreement, and relevant SOWs/Addenda) is logged in Customer Records.
2. The core contract parameters (Subscription Tier, Term, Provisioned Volume, Billing Contact, and Technical Contact) are validated.
3. Financial baseline verification is completed by Finance Operations (or explicit waiver recorded). Financial baseline verification confirms that the contracted fees, billing frequency and billing profile are correctly set up in the Finance & Invoicing System; it does not require receipt of payment unless the contract makes payment a condition of service.

##### "Onboarding Complete"
An account achieves **Onboarding Complete** status when:
1. Platform provisioning and tenant isolation meet the technical parameters specified in the contract.
2. Primary integration pathways are active and transmitting validated test or production payloads without unhandled system exceptions.
3. The designated customer team has completed administrative enablement or sign-off.
4. Formal Go-Live criteria (Section 10) are signed off by the assigned Onboarding owner.

---

### 3. Roles and Functional Responsibilities

| Role | Operational Scope & Primary Responsibilities |
| :--- | :--- |
| **Onboarding Manager (OM)** | Overall process owner for Business and Enterprise implementations. Responsible for milestone tracking, customer kickoff execution, cross-departmental coordination, and sign-off verification. |
| **Customer Success Manager (CSM)** | Account relationship holder. Manages long-term health scoring, executive stakeholder engagement, customer risk tracking, and commercial handoff. |
| **Solutions Engineer (SE)** | Technical authority for architecture design, complex workflow schema mapping, custom API connector configuration, and technical validation. |
| **Security Lead / Analyst** | Executes mandatory security evaluations, regulatory environment verification (e.g., HIPAA/GDPR data residency), custom DPA reviews, and IP whitelist authoring. |
| **Finance Operations** | Validates contract fee structures, verifies tax exempt documentation, schedules recurring invoicing, and processes platform tier entitlement tokens. |
| **Support Lead** | Coordinates Tier 2/3 operational queue assignment, establishes initial incident escalation rosters, and validates customer support SLA entitlement tags. |

---

### 4. Tier-Specific Onboarding Workflows

The operational pathways for onboarding vary structurally by customer tier. The governing contract determines the applicable path:

```
                  +-----------------------------------+
                  | Executed Contract Received in CRM |
                  +-----------------------------------+
                                    |
                                    v
                       [ Evaluate Customer Tier ]
                                    |
         +--------------------------+--------------------------+
         |                          |                          |
         v                          v                          v
   +-----------+              +-----------+              +------------+
   | STANDARD  |              | BUSINESS  |              | ENTERPRISE |
   +-----------+              +-----------+              +------------+
         |                          |                          |
• Automated Tenant         • Named OM Assigned        • Dedicated OM & SE Assigned
  Provisioning             • Managed Kickoff Session  • Security Review Mandatory
• Self-Serve Documentation • Integrated Workflow       • Custom Architecture & SOW
• Pooled Support Queue       Validation               • Priority SLA & Escalations
```

#### 4.1 Standard Tier
* **Workflow Model:** Automated & Self-Directed.
* **Resource Allocation:** No dedicated OM or SE assigned. Operational tasks are managed through automated system provisioning and pooled support queue assistance.
* **Onboarding Schedule:** Expected completion within 14 calendar days of execution.
* **Kickoff:** Automated welcome documentation and system credentials issued to the designated Technical Contact.

#### 4.2 Business Tier
* **Workflow Model:** Managed Onboarding.
* **Resource Allocation:** Assigned named OM for 45 calendar days post-execution. SE resources allocated on a pooled basis for integration validation.
* **Onboarding Schedule:** Standard 30-to-45-day deployment lifecycle.
* **Kickoff:** Formal structured video kickoff meeting required within 7 business days of contract execution.

#### 4.3 Enterprise Tier
* **Workflow Model:** Bespoke Strategic Deployment.
* **Resource Allocation:** Dedicated OM, dedicated CSM, and assigned Solutions Engineer allocated for the duration of the Statement of Work (SOW).
* **Onboarding Schedule:** Tailored timeline defined within the Statement of Work (typically 60–90 days).
* **Kickoff:** Executive alignment kickoff session required before customer-facing system access is released. Internal provisioning and configuration may proceed beforehand, subject to the dependencies in Section 7.

#### 4.4 Default Service Levels by Tier
The following apply only where the executed contract is silent. A contracted SLA, including any custom uptime or response commitment, always governs that customer and is the value tagged in Customer Support and Customer Records.

| Tier | Uptime Target | P1 Initial Response | Support Coverage |
| :--- | :--- | :--- | :--- |
| Standard | 99.5% | 8 business hours | Business hours, pooled queue |
| Business | 99.9% | 4 hours | Extended business hours |
| Enterprise | 99.95% | 15 minutes | 24/7/365 |

---

### 5. Conditional Operational Requirements & Risk Triggers

When evaluating a customer contract for onboarding execution, the assigned operational team must evaluate the presence of the following conditions and trigger mandatory sub-processes accordingly:

```
+------------------------------------+-------------------------------------------+-----------------------------------------------+
| Condition / Contract Parameter     | Operational Trigger Requirement            | Responsible Functional Owner                  |
+------------------------------------+-------------------------------------------+-----------------------------------------------+
| Enterprise Tier Selection          | Mandatory Security & Compliance Review    | Security Lead                                 |
| Regulated Data (HIPAA, PCI, Financial)| Environment Encryption & DPA Verification | Security Lead                                 |
| Executed Volume > 500,000 tx/mo    | Infrastructure Capacity Review            | Solutions Engineer / Infrastructure Operations       |
| Custom API Connector Required      | Technical Architecture Assessment         | Solutions Engineer                            |
| Implementation Window < 14 Days    | Timeline Risk & Resource Escalation       | Onboarding Manager / Department Lead          |
| Multi-Region Data Hosting          | Multi-Tenant Region Lock Provisioning     | Infrastructure Operations                     |
+------------------------------------+-------------------------------------------+-----------------------------------------------+
```

#### 5.1 Mandatory Security Review
* **Trigger:** Applies to all **Enterprise Tier** accounts, OR any account where contract addenda specify handling of HIPAA (PHI), PCI-DSS, or custom restricted regulatory workloads.
* **Requirement:** Operational tenant provisioning **must remain blocked** in Northstar Operations until the Security Lead signs off on environmental access policies, IP whitelisting rules, and signed Data Processing Agreements (DPAs). Internal project planning, task scaffolding and non-production staging that involves no customer data are not blocked and may proceed in parallel.

#### 5.2 Capacity and Infrastructure Assessment
* **Trigger:** Any account contracted for greater than **500,000 workflow execution transactions per month** or containing custom dedicated worker node allocations.
* **Requirement:** Solutions Engineering and Infrastructure Operations must conduct a workload sizing review to confirm cluster capacity before high-volume batch jobs are authorized in production.

#### 5.3 Technical Integration Review
* **Trigger:** Customer contracts specifying non-standard API integrations, legacy database connectors, or custom script executions.
* **Requirement:** The assigned SE must conduct an architectural evaluation of the customer’s target endpoint stability and payload specifications before configuring production connectors in Northstar Operations.

#### 5.4 Accelerated Timeline Risk Escalation
* **Trigger:** Contracts with a target Go-Live date **less than 14 calendar days** from the contract execution date for Business or Enterprise tiers.
* **Requirement:** Requires immediate escalation to the Customer Operations Lead to determine resource availability (escalation category 3.11, POL-OPS-009). If unapproved, standard deployment timelines apply and the contractual Go-Live date is recorded as at risk.
* **Compressed but workable windows:** For Enterprise accounts with a target Go-Live between 14 and 29 calendar days from execution, escalation is not required, but the OM must record the timeline risk in the project plan and flag any dependency (security review, capacity review, custom connector) that cannot complete inside the window.

---

### 6. Contract-Derived Configuration Parameters

All onboarding workflows are strictly governed by the signed customer agreement. The assigned OM or CSM must extract and validate the following values from the agreement in Customer Records prior to initializing project tasks:

1. **Purchased Platform Tier:** Dictates support channel entitlement, SLA thresholds, and resource assignment.
2. **Licensed Seat & Usage Volume Allowance:** Sets administrative thresholds in Northstar Operations.
3. **Contractual Go-Live / Target Implementation Date:** Defines project scheduling and milestone constraints.
4. **Contracted SLA Level:** Establishes ticket priority tags and alert escalation paths in Support system queues.
5. **Required Connectors & Integrations:** Dictates SE task mapping and sandbox configuration scope.
6. **Designated Operational Contacts:**
   * **Primary Billing Contact:** Required for invoice routing and credit checks.
   * **Primary Technical Contact:** Required for administrative credential delivery, sandbox access, and integration sign-off.
7. **Special Commitments / Custom Addenda:** Any custom clauses (e.g., custom uptime credits, specialized backup retention, data residency limits) must be tagged in Customer Records.

---

### 7. Task Dependencies and Sequencing Rules

Operations personnel must adhere to strict task dependencies. Prerequisite tasks must be formally logged as completed before dependent actions are initiated:

```
[ Financial & Contact Verification ]
                 │
                 ▼
[ Tenant Provisioning & Setup ] ◄─── (Blocked if Security Review Required)
                 │
                 ▼
[ Technical Connector Configuration ]
                 │
                 ▼
[ Kickoff Session Execution ] ◄───── (Requires Verified Technical Contact)
                 │
                 ▼
[ Integration Validation Testing ]
                 │
                 ▼
[ Customer Admin Training ]
                 │
                 ▼
[ Production Go-Live Sign-Off ]
```

1. **Prerequisite to System Provisioning:** Internal tenant environment provisioning in Northstar Operations must not occur until Financial Verification is complete AND mandatory Security Reviews (if triggered under Section 5.1) are approved.
2. **Prerequisite to Kickoff Session:** A Kickoff meeting cannot be formally conducted or marked complete until both the **Technical Contact** and **Billing Contact** are verified in Customer Records.
3. **Prerequisite to External Communication:** External welcome communications containing system credentials must never be dispatched prior to the successful verification of internal tenant provisioning and configuration of environment domain isolates and, for Enterprise accounts, completion of the executive alignment kickoff (Section 4.3). Kickoff scheduling and general project-schedule messages are not credential communications and may be sent earlier, subject to the Communication Policy (POL-COM-005).
4. **Prerequisite to Go-Live:** Production credentials and live payload routing must not be enabled until Integration Validation Testing is successfully executed and documented in the Project System.

---

### 8. Critical Information Handling & Ambiguity Resolution

#### 8.1 Critical Baseline Information
The following elements are mandatory for onboarding initialization:
* Customer Legal Name and Operating Jurisdiction.
* Validated Billing Contact Email.
* Validated Technical Contact Email.
* Explicit Subscription Tier Identification.

#### 8.2 Discrepancy Resolution Protocol
If critical baseline information is missing or conflicts between Customer Records and the executed contract:
* **Stop Work Order:** The onboarding workflow must be paused in the Project & Task System.
* **Clarification Ticket:** The OM or CSM must issue a formal clarification ticket to the responsible Account Executive (AE) and Finance Operations Specialist.
* **Prohibition of Assumptions:** Personnel are strictly prohibited from entering default values for missing contact details, geographic data residency options, or SLA tiers.

#### 8.3 Non-Critical Defaults
For non-critical deployment parameters where preferences are unspecified by the customer (e.g., standard workflow alert batching frequencies or default dashboard layout schemas), teams may apply standard system defaults as defined in internal Knowledge Repository guidebooks.

---

### 9. Edge Cases, Exceptions & Escalations

#### 9.1 Existing Account Entity Match
If a newly signed customer matches an existing Account record in Customer Records (e.g., a child subsidiary or previous customer record):
* Do not generate a duplicate root Account entity.
* Associate the new contract under the existing Account entity structure: a former or expanding customer entity is updated rather than duplicated, and a separate legal subsidiary is created as a Child Account linked to its parent (POL-OPS-004 §2.3). Where the parent-subsidiary relationship is not stated in the contract or confirmed by Revenue Operations, treat the match as an ambiguous customer identity (POL-OPS-009 §3.2).
* Review historic configuration settings to prevent legacy environment parameters from overwriting new tenant permissions.

#### 9.2 Contract Amendment / Mid-Onboarding Scope Change
If a customer executes an amended contract or SOW while onboarding is active:
* The OM must immediately log a contract amendment task in the Project & Task System.
* Re-evaluate Section 5 triggers (e.g., tier changes, new regulated workload scope).
* Adjust project target dates to reflect the revised scope.

#### 9.3 Shortened Start Date / High-Urgency Mandates
If an AE executes a contract promising immediate or retroactive system delivery without prior Operations scheduling:
* Issue a **Priority 1 Operational Escalation** to the Operations Lead.
* Determine if emergency temporary SE/OM resources can be assigned.
* Document any resulting impact on established SLAs in Customer Records.

---

### 10. Formal Onboarding Completion Criteria

Onboarding **cannot** be closed out merely by concluding a fixed number of days or marking administrative tasks as finished. An account is formally declared **Onboarding Complete** only when ALL of the following business criteria are validated:

1. **Provisioning Audit Passed:** Active tenant entitlements in Northstar Operations match the contracted seat counts, tier features, and volume limits.
2. **Technical Validation Verified:** At least one core workflow or API integration has executed a end-to-end payload run in production without unhandled system errors over a continuous 48-hour observation window.
3. **Contact Verification Recorded:** Validated Technical Contact and Billing Contact records exist in Customer Records with completed authentication profiles.
4. **Handover Sign-off Executed:**
   * **For Standard Tier:** Automated system enablement email confirmed delivered.
   * **For Business / Enterprise Tier:** Formal written sign-off (via email or signed deployment completion document) received from the customer Technical Contact, OR explicit OM operational handover logged in Customer Records.
5. **Support & CSM Queue Assignment:** Account tags and SLA routing rules are active in Customer Support and Customer Success platforms.

---

### 11. Operational Guidance & Cautions

* **Avoid Out-of-Scope Configurations:** Do not configure custom integrations or extend implementation support beyond what is explicitly documented in the executed Order Form or Statement of Work without an executed Change Order.
* **Auditability:** All customer approval emails, security review sign-offs, and technical schema validations must be linked directly to the account project record in the Project & Task System.
* **Data Privacy Boundaries:** Operational staff must strictly enforce role-based access during setup. Never use actual production customer payload data in sandbox or staging testing environments.

---

### 12. Version History and Approvals

| Version | Date | Author / Title | Description of Changes |
| :--- | :--- | :--- | :--- |
| **3.0** | May 10, 2025 | Operations & Governance | Major revision introducing tier-specific onboarding pathways. |
| **3.2** | Oct 14, 2025 | Security & Compliance | Added mandatory Security Review triggers for Enterprise/regulated data accounts. |
| **3.4** | Feb 01, 2026 | Dir. Customer Experience | Refined Go-Live criteria, task dependency rules, and ambiguity escalation protocols. |

---

*For questions regarding the interpretation or execution of this SOP, consult the Customer Operations Manager or submit an internal ticket to Governance & Operations.*
