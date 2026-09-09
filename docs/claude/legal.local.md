# Legal Playbook Configuration — Switzerland (OpenCaseLaw template v0.1)

This playbook adapts Anthropic's Claude for Legal plugin to Swiss law. Copy it to
`.claude/legal.local.md` in your project (Claude Code) or into a folder you share
with Cowork; the plugin's contract review, NDA triage, response and compliance
skills read it as your organisation's playbook.

It is a template: values in [brackets] are yours to set. It is not legal advice.
Statute references were retrieved from the Fedlex mirror through OpenCaseLaw on
2026-09-09 (OR consolidated 2026-01-01 with amendments pending 2026-10-01 and
2027-07-01; ZGB 2026-07-01; DSG 2025-07-07; UWG 2025-01-01; StGB 2026-06-12;
URG 2025-07-01; IPRG 2026-01-01; ZPO 2026-07-01; KG 2023-07-01). Re-verify before
relying on them. Items marked [practice note, not statute-verified] are drafting
practice, not statute text.

## Jurisdiction and verification rule

- Jurisdiction: Switzerland. Federal private law (OR, ZGB), data protection (DSG),
  unfair competition (UWG), cartels (KG), civil procedure (ZPO), conflict of laws
  (IPRG). Canton of seat: [ZH]. Contract languages: DE, FR, IT, EN.
- VERIFY BEFORE YOU WRITE. Whenever this playbook or your analysis states what a Swiss
  provision says, retrieve the article first with the OpenCaseLaw connector
  (`get_law` for federal law, `get_legislation` for cantonal law; `as_of` for the
  text in force at a past date). Take case citations only from `cite` or
  `find_leading_cases`. Quote only text returned by `get_law`, `get_erwaegung` or
  `get_regeste`. Run `attest_response` on any analysis that cites a decision or an
  article before returning it. If the OpenCaseLaw connector is not connected, say
  so and label every statute statement "unverified".
- Cite as "Art. 100 Abs. 1 OR" (DE) or "art. 100 al. 1 CO" (FR). Never cite from
  memory; recall of Swiss article numbers is unreliable.

## Contract Review Positions

### Limitation of Liability
- Standard position: mutual cap at [12] months of fees paid or payable; indirect and
  consequential damages excluded for both parties.
- Acceptable range: [6–24] months of fees.
- Statutory floor (mandatory): Art. 100 Abs. 1 OR — an advance agreement excluding
  liability for unlawful intent or gross negligence is void. Art. 100 Abs. 2 OR — the
  court may also void an advance waiver for slight negligence where the waiving party
  was in the other party's service or the liability arises from a licensed business.
  Art. 101 OR, Abs. 2 and 3 — liability for auxiliary persons may be limited or
  excluded in advance, but in the service and licensed-business cases at most for
  slight negligence.
- Escalation trigger: uncapped liability; a cap below [6] months; any wording that
  purports to exclude gross negligence or intent (void, and a signal that the drafter
  did not work from Swiss law).
- Redline note to offer: "Nothing in this clause limits liability for unlawful intent
  or gross negligence (Art. 100 OR)."

### Warranty for defects (sale of goods)
- Statutory floor: Art. 199 OR — an agreement excluding or limiting warranty is invalid
  where the seller fraudulently concealed the defects. Art. 210 OR — warranty claims
  prescribe two years after delivery (five years for goods integrated into an immovable
  work); shortening below two years (one year for used goods) is invalid where the goods
  are for the buyer's personal or family use and the seller acts professionally.
- Escalation trigger: warranty exclusions in consumer-facing terms; exclusions paired
  with knowledge of defects.

### Indemnification
- Standard position: mutual indemnification (Schadloshaltung) for third-party IP
  infringement claims and for data-protection breaches; subject to the liability cap
  except for intent and gross negligence.
- Acceptable: indemnification limited to third-party claims; defence control with the
  indemnifying party.
- Escalation trigger: unilateral indemnity for "any breach"; uncapped indemnity outside
  IP and data.
- Note: Swiss law has no separate indemnity doctrine; the Art. 100 OR floor applies to
  indemnities as to any liability clause.

### IP Ownership
- Standard position: each party retains pre-existing IP; deliverables are assigned or
  licensed as expressly agreed; feedback licences narrowly scoped.
- Statutory background: Art. 332 OR — inventions and designs made by an employee in the
  course of work and in performance of contractual duties belong to the employer;
  acquiring other work-related inventions requires a written agreement, written notice
  and a special reasonable compensation. Art. 17 URG — for computer programs created in
  an employment relationship in the course of work duties, the employer alone may
  exercise the exclusive rights of use.
- Escalation trigger: contractor or agency deliverables without an express assignment
  (there is no work-for-hire rule for contractors); assignment of the customer's
  pre-existing IP; unrestricted feedback licences.

### Data Protection
- Applicable law: Datenschutzgesetz vom 25. September 2020 (DSG, SR 235.1). Whether the
  GDPR also applies is a separate territorial-scope question this playbook does not
  answer; do not replace the DSG with GDPR terms for Swiss data subjects.
- Standard position: a data processing agreement is required whenever a vendor processes
  personal data on our behalf (Auftragsbearbeiter). Art. 9 DSG — the processor may
  process only as the controller itself could and only where no statutory or contractual
  secrecy duty forbids the transfer; the controller must satisfy itself that the
  processor can guarantee data security (Art. 8 DSG); sub-processing only with the
  controller's prior approval.
- Cross-border transfers: Art. 16 DSG — transfer abroad is permitted where the Federal
  Council has found adequate protection; otherwise with a treaty, data-protection clauses
  in a contract notified to the EDÖB beforehand, standard data-protection clauses the
  EDÖB has approved, issued or recognised, or binding corporate rules approved
  beforehand. Art. 17 DSG — exceptions include explicit consent, a direct connection with
  the conclusion or performance of a contract, and establishing or enforcing legal
  claims before a foreign court or authority. [practice note, not statute-verified: the
  EU standard contractual clauses are used with a Swiss annex as recognised clauses.]
- Breach notification: Art. 24 DSG — the controller notifies the EDÖB "so rasch als
  möglich" of a breach likely to lead to a high risk for the data subject; the processor
  notifies the controller "so rasch als möglich"; data subjects are informed where needed
  for their protection or where the EDÖB requires it. There is no statutory 72-hour rule
  in the DSG; do not state one. Contract position: the processor notifies within [24–48]
  hours.
- Records and assessments: Art. 12 DSG — controllers and processors keep a record of
  processing activities (the Federal Council exempts companies with fewer than 250
  employees whose processing carries a low risk). Art. 22 DSG — a data protection impact
  assessment (DSFA) before processing that may carry a high risk. Art. 19 DSG —
  information duty when collecting personal data. Art. 14 DSG — foreign controllers
  designate a representative in Switzerland under the listed conditions.
- Why it matters: Art. 61 DSG — fines up to CHF 250,000 on private persons, on complaint,
  for intentionally transferring data abroad without the Art. 16/17 safeguards, engaging
  a processor without the Art. 9 conditions, or failing the minimum security
  requirements. Art. 60 Abs. 1 DSG — the same fine, on complaint, for intentionally false
  or incomplete information or for omitting the Art. 19/21 information; Art. 60 Abs. 2
  DSG — the same fine, without a complaint requirement, for false information or refused
  cooperation in an EDÖB investigation. (Art. 62 and 63 not retrieved; verify before
  citing.)
- Escalation trigger: no DPA offered; transfer to a country without adequacy and without
  Art. 16 guarantees; a GDPR-only DPA that ignores the DSG for Swiss data subjects.

### Confidentiality
- Standard position: mutual; obligations survive [3] years after termination; trade
  secrets for as long as they remain secret.
- Statutory background: Art. 321a Abs. 4 OR — an employee may not exploit or disclose
  secrets learned in service during employment and remains bound after it ends insofar
  as the employer's legitimate interests require. Art. 162 StGB — betraying a
  manufacturing or business secret one was bound to keep is a criminal offence on
  complaint. Art. 6 UWG — exploiting or disclosing secrets obtained unlawfully is unfair
  competition.

### Term and Termination
- Standard position: [annual] term; [30] days' termination for convenience; auto-renewal
  acceptable with a notice window of at least [60] days; consumer-facing general terms
  must not create a significant and unjustified imbalance contrary to good faith
  (Art. 8 UWG).
- Statutory floor (mandatory): Art. 404 OR — a mandate (Auftrag) may be revoked or
  terminated by either party at any time; termination at an inopportune time obliges the
  terminating party to compensate the resulting damage. BGE 115 II 464 E. 2 holds that
  the free right of revocation in a mandate may neither be waived nor restricted by
  contract, and that this applies to pure mandates and to mixed contracts for which the
  mandate rules on the parties' temporal binding are appropriate. BGE 109 II 462
  (Regeste) affirms the admissibility of a contractual penalty for revocation at an
  inopportune time, so a penalty tied to untimely revocation is not the same as a clause
  excluding revocation. [practice note, not statute-verified: minimum terms and
  exclusivity in consulting or advisory agreements are the clauses that typically
  restrict revocation; a termination fee must be read as either a penalty for untimely
  revocation (admissible in principle, subject to reduction under Art. 163 Abs. 3 OR) or
  a restriction of the right itself (not admissible).] Flag YELLOW and explain; RED
  where the deal economics depend on the lock-in. Counsel characterises the contract
  (mandate, work contract, or mixed) before the clause is judged.
- Escalation trigger: long initial terms with no exit; auto-renewal with a notice window
  under [30] days; no cure period for termination for cause.

### Governing Law
- Preferred: Swiss law; courts of [Zurich]; the Handelsgericht where the dispute is
  commercial (Art. 6 ZPO: the business activity of at least one party is concerned, the
  amount in dispute exceeds CHF 30,000 or the dispute is non-pecuniary, and the parties
  are registered as legal entities in the Swiss commercial register or a comparable
  foreign register; not for employment disputes or the lease of residential or business
  premises).
- Statutory background: Art. 116 IPRG — the contract is governed by the law chosen by the
  parties; the choice must be express or clearly evident. Art. 17 ZPO (domestic) and
  Art. 5 IPRG (international) — forum selection in writing or in a form allowing proof by
  text; exclusive unless the agreement says otherwise; under Art. 5 Abs. 2 IPRG it is
  ineffective where a party is abusively deprived of a Swiss forum. Art. 35 ZPO —
  consumers, tenants of residential or business premises, agricultural lessees and
  employees cannot waive their statutory forums in advance.
- Acceptable: other Swiss cantons; England and Wales, EU member-state or US commercial
  courts for cross-border deals; arbitration seated in Switzerland under the Swiss Rules
  for international contracts.
- Escalation trigger: foreign law and a foreign exclusive forum for a Switzerland-only
  deal; forum waivers by consumers or employees; arbitration in an unusual venue.

### Payment Terms
- Standard position: net [30] days. Art. 104 Abs. 1 OR — default interest of five per
  cent per year on late payment even if the contractual interest is lower; a higher
  contractual rate continues to apply during default (Abs. 2).

### Penalties and liquidated damages
- Art. 163 OR — the parties may fix a contractual penalty (Konventionalstrafe) in any
  amount, but the court reduces excessive penalties. Position: a proportionate penalty is
  YELLOW, not RED, in NDAs and service contracts; escalate where the amount is out of
  proportion to the interest protected.

### Limitation periods
- Art. 127 OR — ten years for all claims for which federal civil law provides nothing
  else. Art. 128 OR — five years for periodic payments such as rent and interest, for
  food and hospitality debts, and for claims from craft work, retail sale, medical care,
  the professional work of lawyers and notaries, and the employment relationship.
  Survival and warranty clauses are read against these periods.

### Form and e-signature
- Art. 13 Abs. 1 OR — a contract for which statute prescribes written form must bear the
  signatures of all persons to be bound. Art. 14 Abs. 2bis OR — only a qualified
  electronic signature combined with a qualified time stamp under the ZertES equals a
  handwritten signature. A standard e-signature (simple or advanced) does not satisfy
  statutory written form.
- Provisions that require statutory written form and therefore a qualified electronic
  signature or wet ink: assignment of claims (Art. 165 Abs. 1 OR), an employee
  non-compete (Art. 340 Abs. 1 OR), the employer's reservation of employee inventions
  (Art. 332 Abs. 2 OR). Add to the pre-signature checklist: "Does any provision require
  statutory written form? If yes, route for a qualified electronic signature or wet-ink
  signature."

### Record retention
- Art. 958f OR — business books, accounting records, the annual report and the audit
  report are kept for ten years from the end of the financial year. Return and
  destruction clauses need a retention exception for this.

## NDA Defaults
- Mutual obligations required unless we only disclose.
- Term: [3] years; survival [3–5] years; trade secrets while secret.
- Standard carveouts: public knowledge, prior possession, independent development,
  third-party receipt, legal compulsion (with notice where permitted).
- Permitted disclosures: employees, advisers and affiliates with a need to know, bound by
  equivalent obligations.
- Return and destruction with a retention exception for statutory record keeping
  (Art. 958f OR) and backups.
- Non-compete or non-solicit in a business-to-business NDA: not per se prohibited, but
  check Art. 27 Abs. 2 ZGB (no one may restrict the use of their freedom to a degree that
  violates law or morals) and Art. 5 KG (agreements between actual or potential
  competitors on prices, quantities or the allocation of markets by territory or
  partner are presumed to eliminate effective competition). RED where the counterparty
  is a competitor.
- Employee non-compete clauses: Art. 340 OR — in writing, and binding only where the
  employee had insight into customers or manufacturing or business secrets whose use
  could seriously harm the employer; Art. 340a OR — limited in place, time and subject,
  more than three years only in special circumstances, the court may reduce an excessive
  clause; Art. 340b OR — damages, an agreed penalty, and removal of the breach only where
  specifically agreed in writing and justified; Art. 340c OR — the clause lapses where
  the employer demonstrably has no significant interest left, or terminates without a
  reason attributable to the employee, or the employee terminates for a reason the
  employer is responsible for.
- Penalty clauses: acceptable if proportionate (Art. 163 OR); flag if excessive.
- Governing law Swiss; forum [Zurich]; text form suffices for the forum clause
  (Art. 17 Abs. 2 ZPO).

## Data Subject Requests (Auskunftsbegehren)
- Art. 25 DSG — anyone may ask a controller whether personal data about them is
  processed; the minimum content of the answer is listed in Abs. 2; the controller
  remains responsible when a processor processes the data (Abs. 4); no advance waiver
  (Abs. 5); free of charge as a rule (Abs. 6); "in der Regel innerhalb von 30 Tagen"
  (Abs. 7). Health data may be communicated through a designated health professional
  with the data subject's consent (Abs. 3).
- Art. 60 Abs. 1 lit. a DSG — intentionally false or incomplete information is
  punishable by a fine up to CHF 250,000 on complaint.
- Template adjustments for the plugin's data-subject responses: cite the DSG, not the
  GDPR, for Swiss data subjects; deadline 30 days; supervisory authority EDÖB
  (Eidgenössischer Datenschutz- und Öffentlichkeitsbeauftragter).
- Escalation: health or other sensitive data, litigation hold, current or former
  employees with an active dispute, requests from authorities.

## Compliance overlay (Swiss column for the compliance-check tables)
| Topic | GDPR (plugin default) | Swiss DSG (verify with get_law before use) |
|---|---|---|
| Record of processing | Art. 30 | Art. 12 DSG (exemption below 250 employees, low risk) |
| Impact assessment | Art. 35 | Art. 22 DSG (DSFA) |
| Breach notification | 72 hours | Art. 24 DSG: "so rasch als möglich" to the EDÖB where high risk |
| Processor contract | Art. 28 | Art. 9 DSG |
| International transfer | Chapter V | Art. 16 and 17 DSG |
| Privacy notice | Art. 13/14 | Art. 19 DSG |
| Access request | Art. 15, one month | Art. 25 DSG, 30 days as a rule, free of charge |
| Representative | Art. 27 | Art. 14 DSG |
| Sanctions | administrative fines | Art. 60 and 61 DSG: up to CHF 250,000 on private persons (on complaint, except Art. 60 Abs. 2) |

## Response Templates
- Templates directory: [./templates/] (DE, FR, EN). Until configured, the plugin's
  defaults apply with the Swiss adjustments above.

## Risk framework adjustments
- Where the risk matrix asks for "precedent", check `find_leading_cases` or
  `search_decisions` rather than estimating; cite through `cite`.

## Verification record
Retrieved with `get_law` on 2026-09-09 (federal, German text): OR (SR 220) Art. 13, 14,
100, 101, 104, 127, 128, 163, 165, 199, 210, 321a, 332, 340, 340a, 340b, 340c, 404, 958f;
ZGB (SR 210) Art. 27; DSG (SR 235.1) Art. 8, 9, 12, 14, 16, 17, 19, 22, 24, 25, 60, 61;
UWG (SR 241) Art. 6, 8; StGB (SR 311.0) Art. 162; URG (SR 231.1) Art. 17; IPRG (SR 291)
Art. 5, 116; ZPO (SR 272) Art. 6, 17, 35; KG (SR 251) Art. 5. Decisions checked with
`cite` and `check_claim_support`: BGE 115 II 464 E. 2, BGE 109 II 462. The
machine-readable list is `verified-references.json` next to this file; the repository
test `tests/test_claude_legal_playbook.py` fails when the two drift apart.
