"""The 25 decision records before their legal basis is resolved.

Everything here is the record's own words (decision, purpose, facts,
propositions, objections). Legal authorities appear only as *lookups*:
the arguments of an OpenCaseLaw call. `build.py records` runs each lookup,
stores the tool output in fixture/sources/, and copies the authority's
fields (law, article, edition, text, citation string, pinpoint) from that
output into fixture/records/. Nothing in a lookup is published as a
citation.

Lookup kinds:
  {"get_law": {...}}            statute; as_of defaults to decided_at; "paragraph" picks the
                                numbered paragraph whose verbatim text is copied
  {"cite": {...}}               decision; decision_id + pinpoint, verified by cite
  {"practice": {...}}           administrative practice; search_practice query + doc_id
  {"foreign": {...}}            foreign law, described by the company's own document; not
                                in the corpus, watched by the review date only (T14)
"""

GC = {"person": "gc", "name": "Matteo Bianchi", "role": "General Counsel"}
ACC = {"person": "acc", "name": "Prof. Dr. Thomas Bärtschi", "role": "Chair of the Audit Committee"}
CHAIR = {"person": "chair", "name": "Dr. Regula Imhof", "role": "Chair of the Board"}
CFO = {"person": "cfo", "name": "Annika Zwahlen", "role": "Chief Financial Officer"}
CEO = {"person": "ceo", "name": "Lukas Oberholzer", "role": "Chief Executive Officer"}
RA = {"person": "ra", "name": "Dr. Nadia Rüegg", "role": "Head of Regulatory Affairs and Quality"}
CDO = {"person": "cdo", "name": "Samuel Okafor", "role": "Chief Digital Officer"}
DPO = {"person": "dpo", "name": "Corinne Steiner", "role": "Data Protection Officer"}
US_COUNSEL = {"person": "us_counsel", "name": "Jordan Pryce", "role": "Associate General Counsel, Sernfeld Drives Inc."}

BOARD = "Board of Directors of Sernfeld Antriebstechnik AG"
PRC = "Product Release Committee of Sernfeld Antriebstechnik AG"

FACILITY_2019 = "dms://treasury/facility-agreement-2019.md"
FACILITY_2024 = "dms://treasury/facility-agreement-2024.md"

RECORDS = [
 {
  "id": "rec_01_dividend_fy2019", "type": "board.dividend", "decided_at": "2020-03-26", "decided_by": BOARD,
  "minutes": "dms://board/2020-03-26_minutes.md#4",
  "text": "Proposal to the AGM of a reduced ordinary dividend of CHF 2.00 per share (CHF 4.0m) for financial year 2019, payable only if the liquidity plan of 30 April 2020 holds the minimum liquidity in the base case.",
  "purpose": "Pay shareholders a dividend from the 2019 profit without endangering liquidity during the Covid-19 shutdown.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Payment only if the updated liquidity plan as of 30 April 2020 keeps the CHF 40m minimum liquidity in the base case"],
  "facts": [
   {"id": "f1", "text": "Balance-sheet profit of CHF 96.5m per audited 2019 accounts", "source": "dms://finance/2019/audited-accounts-extract.md", "as_of": "2019-12-31"},
   {"id": "f2", "text": "Stress case of the liquidity plan falls to CHF 34m in October 2020 (CHF 38m without the dividend), below the CHF 40m minimum", "source": "dms://finance/2020/liquidity-forecast-2020-03.md", "as_of": "2020-03-24"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "2",
    "proposition": "Dividends may be paid only out of the balance-sheet profit and out of reserves created for this purpose."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "698"}, "paragraph": "2",
    "proposition": "The general meeting, not the board, resolves on the appropriation of the balance-sheet profit and the dividend."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_140_III_533", "pinpoint": "3"},
    "proposition": "Shareholders who receive an unjustified distribution can be required to return it, in addition to the directors' liability."},
  ],
  "commitments": [
   {"id": "c1", "text": "Distributions only if pro-forma leverage does not exceed 2.75x (clause 23.4)", "source": FACILITY_2019 + "#23.4", "test_date": "2020-05-14"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "The full dividend of CHF 3.00 should be paid; the balance sheet is strong.", "answer": "Reduced to CHF 2.00 because the stress case breaches the minimum liquidity; recorded as a statement in the minutes."},
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "The dividend must not endanger the company as a going concern.", "answer": "Payment made conditional on the 30 April 2020 liquidity plan."},
  ],
  "review": {"date": "2021-03-31", "reopen_if": ["l1 or l2 amended", "l3 qualified or overruled", "f2 liquidity plan breaches the minimum in the base case", "c1 test failed"]},
 },
 {
  "id": "rec_02_service_gmbh_subordination_2020", "type": "board.capital_loss", "decided_at": "2020-05-28", "decided_by": BOARD + " (as sole shareholder and creditor of Sernfeld Service GmbH)",
  "minutes": "dms://board/2020-05-28_minutes.md#3",
  "text": "Subordination of the parent's CHF 6.0m loan to Sernfeld Service GmbH so that the GmbH, over-indebted by CHF 1.9m at going-concern values, need not notify the court; quarterly interim balance sheets.",
  "purpose": "Keep Sernfeld Service GmbH operating through the Covid-19 downturn without a bankruptcy filing while a restructuring is prepared.",
  "owner": CFO, "fallback_owner": GC,
  "restrictions": ["The subordination stays in place as long as the GmbH is over-indebted"],
  "facts": [
   {"id": "f1", "text": "Interim balance sheet of 30 April 2020: equity CHF -1.9m at going-concern values, CHF -3.4m at liquidation values", "source": "dms://finance/service-gmbh/interim-balance-2020-04-30.md", "as_of": "2020-04-30"},
   {"id": "f2", "text": "Subordinated parent loan of CHF 6.0m; interest of 2% not covered by the subordination", "source": "dms://finance/service-gmbh/subordination-agreement-2020.md", "as_of": "2020-05-29"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "725"}, "paragraph": "2",
    "proposition": "If the company is over-indebted at going-concern and at liquidation values, the board must notify the court unless creditors subordinate their claims in the amount of the shortfall."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "820"}, "paragraph": None,
    "proposition": "The company-law rules on capital loss and over-indebtedness apply to a GmbH."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_136_III_322", "pinpoint": "3"},
    "proposition": "Damage caused by delaying a bankruptcy filing is measured at liquidation values; directors who delay are liable for it."},
  ],
  "commitments": [
   {"id": "c1", "text": "Quarterly interim balance sheets of the GmbH to the board", "source": "dms://board/2020-05-28_minutes.md#3", "test_date": "2020-09-30"},
  ],
  "objections": [
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "A subordination may not suffice if earnings prospects remain uncertain.", "answer": "The subordination removes the duty to notify; a restructuring is to be examined by mid-2021."},
  ],
  "review": {"date": "2021-06-30", "reopen_if": ["l1 or l2 amended", "l3 qualified", "f1 shortfall exceeds the subordinated amount", "f2 subordination amended or terminated"]},
 },
 {
  "id": "rec_03_sx_ex_release", "type": "product.market_approval", "decided_at": "2020-10-22", "decided_by": PRC,
  "minutes": "dms://products/sx-ex/release-decision-2020.md",
  "text": "Release of the SX-Ex explosion-protected pump drive (II 2G Ex db IIB T4) for sale in Switzerland and the EU/EEA from 1 November 2020.",
  "purpose": "Sell the SX-Ex to chemical and process-industry customers in Switzerland and the EU/EEA with valid conformity for explosive atmospheres.",
  "owner": RA, "fallback_owner": CEO,
  "restrictions": ["Sale only in Switzerland, the EU and the EEA (Norway, Iceland, Liechtenstein)", "Not for zone 0 / category 1G applications"],
  "facts": [
   {"id": "f1", "text": "EU-type examination certificate ExC 20 ATEX 4471 X, surveillance audit every two years", "source": "dms://products/sx-ex/ex-certificate-2020.md", "as_of": "2020-10-02"},
   {"id": "f2", "text": "Declaration of conformity signed 22 October 2020", "source": "dms://products/sx-ex/declaration-of-conformity-2020.md", "as_of": "2020-10-22"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "VGSEB", "article": "3"}, "paragraph": None,
    "proposition": "Equipment for explosive atmospheres may be placed on the market only if it endangers neither persons nor property when properly installed, maintained and used."},
   {"id": "l2", "get_law": {"abbreviation": "PrSG", "article": "3"}, "paragraph": "1",
    "proposition": "Products may be placed on the market only if they endanger the safety and health of users and third parties not at all or only slightly in normal or foreseeable use."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_133_III_81", "pinpoint": "3"},
    "proposition": "A product is defective if it does not offer the safety a person is entitled to expect; the injured party need not prove the cause of the defect."},
  ],
  "commitments": [
   {"id": "c1", "text": "Certificate surveillance audit by the notified body every two years", "source": "dms://products/sx-ex/ex-certificate-2020.md", "test_date": "2022-10-02"},
  ],
  "objections": [],
  "review": {"date": "2022-10-31", "reopen_if": ["l1 or l2 amended", "l3 qualified", "f1 certificate withdrawn or not renewed", "sale outside the approved markets"]},
 },
 {
  "id": "rec_04_dividend_fy2020_ker", "type": "board.dividend", "decided_at": "2021-03-18", "decided_by": BOARD,
  "minutes": "dms://board/2021-03-18_minutes.md#5",
  "text": "Proposal to the AGM of a distribution of CHF 1.50 per share (CHF 3.0m) for financial year 2020 out of the reserves from capital contributions, without withholding tax.",
  "purpose": "Return capital to shareholders without Swiss withholding tax while keeping the 2020 profit in the company.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Distribution only out of the reserves from capital contributions confirmed by the ESTV"],
  "facts": [
   {"id": "f1", "text": "Reserves from capital contributions of CHF 45.0m confirmed by the ESTV as of 31 December 2020", "source": "dms://tax/estv-ker-confirmation-2021.md", "as_of": "2020-12-31"},
   {"id": "f2", "text": "Statutory capital reserve CHF 45.0m and balance-sheet profit CHF 104.6m per audited 2020 accounts", "source": "dms://finance/2020/audited-accounts-extract.md", "as_of": "2020-12-31"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "VStG", "article": "5"}, "paragraph": "1bis",
    "proposition": "Repayments of reserves from capital contributions made by shareholders after 31 December 1996 are exempt from withholding tax if shown separately in the commercial balance sheet."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "2",
    "proposition": "Dividends may be paid only out of the balance-sheet profit and out of reserves created for this purpose."},
   {"id": "l3", "practice": {"query": "Kapitaleinlagereserven Verrechnungssteuer Kreisschreiben", "doc_id": "estv_ks_dbst_ks_2015_1_029a_dvs_de"},
    "proposition": "ESTV practice on the capital contribution principle governs which reserves qualify and how they must be reported."},
  ],
  "commitments": [
   {"id": "c1", "text": "Report every change in the reserves from capital contributions to the ESTV (form 170)", "source": "dms://tax/estv-ker-confirmation-2021.md", "test_date": "2021-06-30"},
  ],
  "objections": [
   {"by": "Peter Kälin (board member)", "text": "What is the tax risk?", "answer": "ESTV confirmation of 12 February 2021 and ESTV practice on the capital contribution principle."},
  ],
  "review": {"date": "2022-03-31", "reopen_if": ["l1 or l2 amended", "l3 new version", "f1 ESTV confirmation revised"]},
 },
 {
  "id": "rec_05_md200_release_ch_eu", "type": "product.market_approval", "decided_at": "2021-05-20", "decided_by": PRC,
  "minutes": "dms://products/md-200/release-decision-2021.md",
  "text": "Release of the MD-200 drive unit as a class I medical device for Switzerland and the EU/EEA from 1 June 2021, with an EU authorised representative.",
  "purpose": "Enter the patient-lift market in Switzerland and the EU/EEA in compliance with the medical-device rules that change on 26 May 2021.",
  "owner": RA, "fallback_owner": CEO,
  "restrictions": ["Sale only in Switzerland and the EU/EEA; not in the USA, the UK or Brazil without a new release"],
  "facts": [
   {"id": "f1", "text": "Class I classification (rule 13) and declaration of conformity of 20 May 2021", "source": "dms://products/md-200/declaration-of-conformity-2021.md", "as_of": "2021-05-20"},
   {"id": "f2", "text": "EU authorised representative MedRep Europe B.V. appointed", "source": "dms://products/md-200/release-decision-2021.md", "as_of": "2021-05-20"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "MepV", "article": "6"}, "paragraph": "1",
    "proposition": "A Swiss manufacturer placing a class I device on the market must notify Swissmedic of the device at the latest when placing it on the market."},
   {"id": "l2", "get_law": {"abbreviation": "MepV", "article": "6", "as_of": "2021-05-26"}, "paragraph": "2",
    "proposition": "From 26 May 2021 devices must meet the general safety and performance requirements of Annex I EU-MDR."},
  ],
  "commitments": [
   {"id": "c1", "text": "Mandate of the EU authorised representative to be kept in force", "source": "dms://products/md-200/release-decision-2021.md", "test_date": "2022-05-31"},
  ],
  "objections": [],
  "review": {"date": "2022-05-31", "reopen_if": ["l1 or l2 amended or repealed", "f1 classification changed", "sale outside the approved markets"]},
 },
 {
  "id": "rec_06_kaelin_supply_2021", "type": "board.conflict_transaction", "decided_at": "2021-06-24", "decided_by": BOARD + " (members without conflict)",
  "minutes": "dms://board/2021-06-24_minutes.md#3",
  "text": "Approval by the non-conflicted directors of a five-year castings supply framework agreement (about CHF 18m a year) with Kälin Giesserei AG, owned by director Peter Kälin, subject to an annual price benchmark.",
  "purpose": "Secure castings at market prices with short lead times while protecting the company from the director's conflict of interest.",
  "owner": GC, "fallback_owner": CHAIR,
  "restrictions": ["Peter Kälin does not sign for the company", "Annual price comparison with at least two independent suppliers"],
  "facts": [
   {"id": "f1", "text": "Benchmark against three independent foundries: Kälin −2.0% to +3.0% of market mean, weighted +0.6%", "source": "dms://contracts/kaelin-benchmark-2021.md", "as_of": "2021-06-15"},
   {"id": "f2", "text": "Peter Kälin declared the conflict and left the meeting", "source": "dms://board/2021-06-24_minutes.md#3", "as_of": "2021-06-24"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "717"}, "paragraph": "1",
    "proposition": "Directors must perform their duties with all due diligence and safeguard the company's interests in good faith."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "718b"}, "paragraph": None,
    "proposition": "A contract between the company and the person representing it must be in writing, except for ordinary-course contracts of small value."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_127_III_332", "pinpoint": "2"},
    "proposition": "A self-dealing transaction of a director can be approved by another director with signing authority."},
  ],
  "commitments": [
   {"id": "c1", "text": "Annual price comparison with at least two independent suppliers (clause 7.2)", "source": "dms://contracts/kaelin-supply-agreement-2021.md#7.2", "test_date": "2022-06-30"},
  ],
  "objections": [
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "Prices must be compared again every year.", "answer": "Accepted; annual benchmark made a condition of the approval."},
  ],
  "review": {"date": "2022-06-30", "reopen_if": ["l1 or l2 amended", "l3 qualified", "c1 benchmark shows Kälin above market", "f2 conflict not respected in execution"]},
 },
 {
  "id": "rec_07_sernsense_predictive_maintenance", "type": "board.ai_deployment", "decided_at": "2021-09-23", "decided_by": BOARD,
  "minutes": "dms://board/2021-09-23_minutes.md#6",
  "text": "Deployment from 1 January 2022 of SernSense, a machine-learning failure prediction on customer drive telemetry processed on a US provider's cloud platform, in Switzerland and the EU.",
  "purpose": "Offer predictive maintenance to customers and reduce unplanned downtime, without processing operators' personal data beyond what is needed.",
  "owner": CDO, "fallback_owner": GC,
  "restrictions": ["Operator IDs pseudonymised before transfer", "No use of the data to assess operators' performance", "Only customers in Switzerland and the EU"],
  "facts": [
   {"id": "f1", "text": "About 9% of installations transmit operator IDs that can identify persons", "source": "dms://ai/sernsense-dpia-2021.md", "as_of": "2021-09-10"},
   {"id": "f2", "text": "Processor agreement with CloudWorks Analytics Inc.; transfer to the USA on standard contractual clauses", "source": "dms://ai/sernsense-dpa-2021.md", "as_of": "2021-09-15"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "DSG", "article": "4"}, "paragraph": "3",
    "proposition": "Personal data may be processed only for the purpose indicated when it was collected, evident from the circumstances or provided by law."},
   {"id": "l2", "get_law": {"abbreviation": "DSG", "article": "6"}, "paragraph": "2",
    "proposition": "Without adequate foreign legislation, personal data may be disclosed abroad only with sufficient safeguards, in particular contractual ones."},
   {"id": "l3", "get_law": {"abbreviation": "DSG", "article": "10a"}, "paragraph": "1",
    "proposition": "Processing may be delegated to a third party by contract if the third party processes the data only as the controller itself may."},
  ],
  "commitments": [
   {"id": "c1", "text": "Processor agreement runs to 31 December 2024 with automatic renewal", "source": "dms://ai/sernsense-dpa-2021.md", "test_date": "2024-12-31"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "Liability risk if predictions are wrong.", "answer": "Predictions are non-binding recommendations; service contracts exclude warranties."},
  ],
  "review": {"date": "2022-09-30", "reopen_if": ["l1, l2 or l3 amended or repealed", "f2 provider or transfer basis changed", "use outside Switzerland and the EU"]},
 },
 {
  "id": "rec_08_dividend_fy2021", "type": "board.dividend", "decided_at": "2022-03-17", "decided_by": BOARD,
  "minutes": "dms://board/2022-03-17_minutes.md#4",
  "text": "Proposal to the AGM of an ordinary dividend of CHF 4.00 per share (CHF 8.0m) for financial year 2021.",
  "purpose": "Distribute part of the 2021 profit while staying well within the facility's distribution test.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Auditor confirms that the proposal complies with law and articles"],
  "facts": [
   {"id": "f1", "text": "Balance-sheet profit of CHF 146.3m per audited 2021 accounts", "source": "dms://finance/2021/audited-accounts-extract.md", "as_of": "2021-12-31"},
   {"id": "f2", "text": "Leverage 1.6x at 31 December 2021", "source": "dms://finance/2021/audited-accounts-extract.md", "as_of": "2021-12-31"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "2",
    "proposition": "Dividends may be paid only out of the balance-sheet profit and out of reserves created for this purpose."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "728a"}, "paragraph": "1",
    "proposition": "The auditor examines whether the proposal on the appropriation of the balance-sheet profit complies with the law and the articles."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_140_III_533", "pinpoint": "3"},
    "proposition": "Shareholders who receive an unjustified distribution can be required to return it, in addition to the directors' liability."},
  ],
  "commitments": [
   {"id": "c1", "text": "Distributions only if pro-forma leverage does not exceed 2.75x (clause 23.4)", "source": FACILITY_2019 + "#23.4", "test_date": "2022-05-12"},
  ],
  "objections": [],
  "review": {"date": "2023-03-31", "reopen_if": ["l1 or l2 amended", "l3 qualified or overruled", "c1 test failed"]},
 },
 {
  "id": "rec_09_cv_screening_pilot", "type": "board.ai_deployment", "decided_at": "2022-06-16", "decided_by": BOARD,
  "minutes": "dms://board/2022-06-16_minutes.md#5",
  "text": "Pilot from 1 August 2022 to 31 July 2023 of AI ranking of applications for apprenticeships and production jobs in Winterthur, with no automatic rejections.",
  "purpose": "Speed up the handling of about 1,800 applications a year without discriminating and with a person deciding on every application.",
  "owner": DPO, "fallback_owner": GC,
  "restrictions": ["Winterthur site only", "No automatic rejection; a person decides on each application", "Sex, age, nationality and photo removed before scoring"],
  "facts": [
   {"id": "f1", "text": "Provider TalentRank AG, data held in Switzerland; ranking is a recommendation only", "source": "dms://ai/cv-screening-pilot-memo-2022.md", "as_of": "2022-06-01"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "328b"}, "paragraph": None,
    "proposition": "An employer may process data about an employee or applicant only insofar as it concerns suitability for the job or is necessary to perform the employment contract."},
   {"id": "l2", "get_law": {"abbreviation": "GlG", "article": "3"}, "paragraph": "2",
    "proposition": "The prohibition of sex discrimination applies in particular to hiring."},
   {"id": "l3", "get_law": {"abbreviation": "DSG", "article": "4"}, "paragraph": "4",
    "proposition": "The collection of personal data and its purposes must be evident to the data subject."},
  ],
  "commitments": [
   {"id": "c1", "text": "Quarterly control of acceptance rates by sex", "source": "dms://ai/cv-screening-pilot-memo-2022.md", "test_date": "2022-10-31"},
  ],
  "objections": [
   {"by": "Dr. Andreas Wyss (board member)", "text": "Risk of discrimination.", "answer": "Protected attributes removed before scoring; quarterly control of acceptance rates by sex."},
   {"by": "Matteo Bianchi (General Counsel)", "text": "The revised data protection act enters into force on 1 September 2023 with new information duties.", "answer": "Pilot to be reviewed before then."},
  ],
  "review": {"date": "2023-06-30", "reopen_if": ["l1, l2 or l3 amended or repealed", "automatic rejections introduced", "use outside Winterthur"]},
 },
 {
  "id": "rec_10_cobot_line_release", "type": "product.market_approval", "decided_at": "2022-09-15", "decided_by": PRC,
  "minutes": "dms://products/cobot-line/release-decision-2022.md",
  "text": "Release of the CoBot-Line collaborative handling cell as a complete machine for Switzerland and the EU/EEA from 1 October 2022, only with the integrated safety scanner.",
  "purpose": "Sell a safe collaborative cell to industrial customers in Switzerland and the EU/EEA.",
  "owner": RA, "fallback_owner": CEO,
  "restrictions": ["Sale only in Switzerland and the EU/EEA", "Only with the integrated safety scanner; no sale of the arm without the cell"],
  "facts": [
   {"id": "f1", "text": "Declaration of conformity under the machinery rules, standards EN ISO 10218-2 and ISO/TS 15066", "source": "dms://products/cobot-line/declaration-of-conformity-2022.md", "as_of": "2022-09-15"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "MaschV", "article": "2"}, "paragraph": "1",
    "proposition": "Machinery may be placed on the market only if it meets the health and safety requirements when properly installed, maintained and used as intended or reasonably foreseeable."},
   {"id": "l2", "get_law": {"abbreviation": "PrHG", "article": "4"}, "paragraph": "1",
    "proposition": "A product is defective if it does not provide the safety a person is entitled to expect, taking all circumstances into account."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_133_III_81", "pinpoint": "3"},
    "proposition": "The injured party need not prove the cause of the defect; it suffices to show that the product did not meet legitimate safety expectations."},
  ],
  "commitments": [],
  "objections": [],
  "review": {"date": "2024-09-30", "reopen_if": ["l1 or l2 amended", "l3 qualified", "arm sold without the cell", "sale outside the approved markets"]},
 },
 {
  "id": "rec_11_hegi_land_sale", "type": "board.conflict_transaction", "decided_at": "2022-11-10", "decided_by": BOARD + " (members without conflict)",
  "minutes": "dms://board/2022-11-10_minutes.md#2",
  "text": "Sale of the non-operating plot 7712 in Winterthur-Hegi to the Imhof Family Foundation, related to the board chair, for CHF 8.4m (independent valuation CHF 8.1–8.6m; best third-party offer CHF 7.9m).",
  "purpose": "Sell a non-operating asset at not less than market value while protecting the company from the chair's conflict of interest.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Completion by 31 March 2023", "Signed by CEO and CFO, not by the chair"],
  "facts": [
   {"id": "f1", "text": "Independent valuation CHF 8.1–8.6m (mean CHF 8.35m), assuming no contaminated-site entry and unchanged zoning", "source": "dms://realestate/appraisal-hegi-2022.md", "as_of": "2022-10-01"},
   {"id": "f2", "text": "Estimated cantonal property gains tax CHF 0.9m", "source": "dms://board/2022-11-10_minutes.md#2", "as_of": "2022-11-10"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "717"}, "paragraph": "1",
    "proposition": "Directors must perform their duties with all due diligence and safeguard the company's interests in good faith."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "678"}, "paragraph": "2",
    "proposition": "Directors and related persons must return other benefits that are manifestly disproportionate to the consideration received from them."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_126_III_361", "pinpoint": "3"},
    "proposition": "Where the interests of the company conflict with those of the acting organ, the organ's power to represent the company is in question unless the transaction is authorised or ratified."},
   {"id": "l4", "get_law": {"canton": "ZH", "sr_number": "631.1", "article": "216"}, "paragraph": "1",
    "proposition": "The cantonal property gains tax is levied on gains from transfers of real estate."},
  ],
  "commitments": [
   {"id": "c1", "text": "Completion of the sale by 31 March 2023", "source": "dms://board/2022-11-10_minutes.md#2", "test_date": "2023-03-31"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "A second valuation is missing.", "answer": "Rejected 3:1; the valuation is independent and the price exceeds the best third-party offer."},
  ],
  "review": {"date": "2023-03-31", "reopen_if": ["l1 or l2 amended", "l3 qualified", "l4 amended", "f1 valuation assumptions prove wrong (contamination, zoning)"]},
 },
 {
  "id": "rec_12_dividend_fy2022", "type": "board.dividend", "decided_at": "2023-03-16", "decided_by": BOARD,
  "minutes": "dms://board/2023-03-16_minutes.md#4",
  "text": "Proposal to the AGM of an ordinary dividend of CHF 4.20 per share (CHF 8.4m) for financial year 2022, the first under the revised company law, payable after the auditor's confirmation.",
  "purpose": "Distribute part of the 2022 profit while accounting for the guarantee exposure from the Hollis claim.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Payment only after the auditor's confirmation of the proposal"],
  "facts": [
   {"id": "f1", "text": "Balance-sheet profit of CHF 183.2m per audited 2022 accounts", "source": "dms://finance/2022/audited-accounts-extract.md", "as_of": "2022-12-31"},
   {"id": "f2", "text": "Hollis claim USD 60m against Sernfeld Drives Inc., parent guarantee, provision CHF 22.0m", "source": "dms://legal/claims/2022-031-hollis.md", "as_of": "2023-03-01"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "2",
    "proposition": "Dividends may be paid only out of the balance-sheet profit and out of reserves created for this purpose."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "3",
    "proposition": "Dividends may be set only after the allocations to the statutory and voluntary retained earnings."},
   {"id": "l3", "get_law": {"abbreviation": "OR", "article": "728a"}, "paragraph": "1",
    "proposition": "The auditor examines whether the proposal on the appropriation of the balance-sheet profit complies with the law and the articles."},
   {"id": "l4", "cite": {"decision_id": "bge_BGE_140_III_533", "pinpoint": "3"},
    "proposition": "Shareholders who receive an unjustified distribution can be required to return it, in addition to the directors' liability."},
  ],
  "commitments": [
   {"id": "c1", "text": "Distributions only if pro-forma leverage does not exceed 2.75x (clause 23.4)", "source": FACILITY_2019 + "#23.4", "test_date": "2023-05-11"},
  ],
  "objections": [
   {"by": "Annika Zwahlen (CFO)", "text": "The Hollis claim could exceed the provision.", "answer": "Provision reviewed by US counsel; reassess if the claim is amended."},
  ],
  "review": {"date": "2024-03-31", "reopen_if": ["l1, l2 or l3 amended", "l4 qualified or overruled", "f2 claim amended or provision raised", "c1 test failed"]},
 },
 {
  "id": "rec_13_service_gmbh_capital_loss_2023", "type": "board.capital_loss", "decided_at": "2023-04-20", "decided_by": BOARD + " (as sole shareholder of Sernfeld Service GmbH)",
  "minutes": "dms://board/2023-04-20_minutes.md#3",
  "text": "Capital contribution of CHF 3.0m to Sernfeld Service GmbH, limited review of its 2022 accounts by a licensed auditor despite the opting-out, and continuation of the 2020 subordination.",
  "purpose": "Remove the capital loss of Sernfeld Service GmbH (38% cover of capital and non-distributable reserves) under the revised company law.",
  "owner": CFO, "fallback_owner": GC,
  "restrictions": ["Contribution paid by 30 June 2023"],
  "facts": [
   {"id": "f1", "text": "2022 accounts of the GmbH: equity CHF 0.76m, 38% cover of nominal capital and non-distributable reserves, no over-indebtedness", "source": "dms://finance/service-gmbh/annual-accounts-2022.md", "as_of": "2022-12-31"},
   {"id": "f2", "text": "Subordination of CHF 6.0m continues", "source": "dms://finance/service-gmbh/subordination-agreement-2020.md", "as_of": "2023-04-20"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "725a"}, "paragraph": "1",
    "proposition": "If assets less liabilities no longer cover half of the capital and non-distributable statutory reserves, the board must take measures to remove the capital loss."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "725a"}, "paragraph": "2",
    "proposition": "A company without an auditor must have its last annual accounts reviewed by a licensed auditor before approval."},
   {"id": "l3", "get_law": {"abbreviation": "OR", "article": "820"}, "paragraph": None,
    "proposition": "The company-law rules on capital loss and over-indebtedness apply to a GmbH."},
  ],
  "commitments": [
   {"id": "c1", "text": "Capital contribution of CHF 3.0m paid by 30 June 2023", "source": "dms://board/2023-04-20_minutes.md#3", "test_date": "2023-06-30"},
  ],
  "objections": [],
  "review": {"date": "2024-03-31", "reopen_if": ["l1, l2 or l3 amended", "f1 capital loss persists in the 2023 accounts", "c1 contribution not paid"]},
 },
 {
  "id": "rec_14_imhof_holding_loan", "type": "board.conflict_transaction", "decided_at": "2023-09-14", "decided_by": BOARD + " (members without conflict)",
  "minutes": "dms://board/2023-09-14_minutes.md#2",
  "text": "Loan of CHF 12.0m for three years at 3.25% to Imhof Beteiligungen AG, the 52% shareholder chaired by the board chair, secured by a pledge of 600,000 company shares; the amount treated as blocked free equity.",
  "purpose": "Earn an arm's-length return on surplus cash without making a hidden distribution to the controlling shareholder.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Immediate termination right if the borrower's credit deteriorates", "Annual review of the interest rate against the ESTV safe-harbour rate", "Loan amount treated as blocked free equity"],
  "facts": [
   {"id": "f1", "text": "Borrower assets about CHF 274m, debt CHF 9m; loan-to-value of the share pledge 40%", "source": "dms://related-party/imhof-holding-credit-memo-2023.md", "as_of": "2023-08-31"},
   {"id": "f2", "text": "Free equity CHF 183.2m at 31 December 2022", "source": "dms://finance/2022/audited-accounts-extract.md", "as_of": "2022-12-31"},
   {"id": "f3", "text": "Loan terms: CHF 12.0m, 3.25%, repayment 30 September 2026, termination if security below 150%", "source": "dms://related-party/imhof-holding-loan-agreement-2023.md", "as_of": "2023-09-20"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "680"}, "paragraph": "2",
    "proposition": "Shareholders have no right to the return of their capital contributions."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "1",
    "proposition": "Directors and managers must inform the board without delay of conflicts of interest affecting them."},
   {"id": "l3", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "2",
    "proposition": "The board must take the measures needed to safeguard the company's interests in case of a conflict of interest."},
   {"id": "l4", "cite": {"decision_id": "bge_BGE_140_III_533", "pinpoint": "4"},
    "proposition": "Intra-group and shareholder loans not at arm's length can breach the prohibition of returning capital contributions and reduce free equity."},
  ],
  "commitments": [
   {"id": "c1", "text": "Loans to non-group persons not to exceed CHF 15m in aggregate (clause 23.6)", "source": FACILITY_2019 + "#23.6", "test_date": "2023-12-31"},
   {"id": "c2", "text": "Repayment of the loan on 30 September 2026", "source": "dms://related-party/imhof-holding-loan-agreement-2023.md", "test_date": "2026-09-30"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "The loan must be terminable immediately if the borrower's credit deteriorates.", "answer": "Accepted; termination right added."},
  ],
  "review": {"date": "2024-09-30", "reopen_if": ["l1, l2 or l3 amended", "l4 qualified or overruled", "f1 borrower's credit deteriorates", "c1 loans-out basket exceeded", "c2 repayment missed"]},
 },
 {
  "id": "rec_15_rc_link_release", "type": "product.market_approval", "decided_at": "2023-10-19", "decided_by": PRC,
  "minutes": "dms://products/rc-link/release-decision-2023.md",
  "text": "Release of the RC-Link drive controller with Bluetooth module for Switzerland, the EU/EEA and the United Kingdom from 1 November 2023.",
  "purpose": "Offer smartphone commissioning of drives in markets where the radio module is approved.",
  "owner": RA, "fallback_owner": CEO,
  "restrictions": ["Sale only in Switzerland, the EU/EEA and the UK", "App not offered in other countries' app stores until the radio module is approved there"],
  "facts": [
   {"id": "f1", "text": "Declaration of conformity for radio equipment with EN 300 328 V2.2.2", "source": "dms://products/rc-link/declaration-of-conformity-2023.md", "as_of": "2023-10-19"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "FAV", "article": "7"}, "paragraph": "1",
    "proposition": "Radio equipment must be built to protect health and safety, ensure electromagnetic compatibility and use the radio spectrum efficiently."},
   {"id": "l2", "get_law": {"abbreviation": "FAV", "article": "9"}, "paragraph": None,
    "proposition": "Conformity with the essential requirements on the use of the frequency spectrum is presumed for equipment built to designated technical standards."},
  ],
  "commitments": [],
  "objections": [],
  "review": {"date": "2025-10-31", "reopen_if": ["l1 or l2 amended", "f1 standard superseded", "sale or app distribution outside the approved markets"]},
 },
 {
  "id": "rec_16_sernfeld_assist_genai", "type": "board.ai_deployment", "decided_at": "2023-11-23", "decided_by": BOARD,
  "minutes": "dms://board/2023-11-23_minutes.md#5",
  "text": "Introduction from 15 January 2024 of the generative AI assistant 'Sernfeld Assist' for 1,200 office workplaces, with no input of personnel, health or customer data and no use for decisions about employees.",
  "purpose": "Increase productivity in drafting, translation and summarising while keeping personal and customer data out of the system.",
  "owner": CDO, "fallback_owner": GC,
  "restrictions": ["No input of personnel data, health data or customer data", "No use for decisions about employees", "Usage logged; training before access"],
  "facts": [
   {"id": "f1", "text": "DPIA: high risk only if personnel or health data are entered; residual risk acceptable with measures", "source": "dms://ai/assist-dpia-2023.md", "as_of": "2023-11-02"},
   {"id": "f2", "text": "Processor Nimbus AI Services Ltd; US sub-processor for abuse monitoring with 30-day retention", "source": "dms://ai/assist-dpa-2023.md", "as_of": "2023-11-08"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "DSG", "article": "7"}, "paragraph": "1",
    "proposition": "The controller must design processing technically and organisationally so that data protection rules are complied with (privacy by design)."},
   {"id": "l2", "get_law": {"abbreviation": "DSG", "article": "9"}, "paragraph": "1",
    "proposition": "Processing may be entrusted to a processor by contract if the processor processes the data only as the controller itself may."},
   {"id": "l3", "get_law": {"abbreviation": "DSG", "article": "16"}, "paragraph": "1",
    "proposition": "Personal data may be disclosed abroad if the Federal Council has found that the recipient state ensures adequate protection."},
   {"id": "l4", "get_law": {"abbreviation": "DSG", "article": "22"}, "paragraph": "1",
    "proposition": "A data protection impact assessment is required where processing may entail a high risk to the personality or fundamental rights of data subjects."},
  ],
  "commitments": [],
  "objections": [
   {"by": "Dr. Andreas Wyss (board member)", "text": "May customer data be entered?", "answer": "No; excluded technically and by instruction."},
  ],
  "review": {"date": "2024-12-31", "reopen_if": ["l1 to l4 amended", "f2 sub-processors or retention changed", "personnel, health or customer data entered", "use for decisions about employees"]},
 },
 {
  "id": "rec_17_interim_dividend_2024", "type": "board.dividend", "decided_at": "2024-08-22", "decided_by": BOARD,
  "minutes": "dms://board/2024-08-22_minutes.md#3",
  "text": "Proposal to an extraordinary general meeting of an interim dividend of CHF 1.00 per share (CHF 2.0m) based on the interim accounts at 30 June 2024, payable only after the auditor's report on the interim accounts.",
  "purpose": "Distribute part of the first-half 2024 profit before year end under the interim-dividend rules of the revised company law.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Payment only after the auditor's report on the interim accounts"],
  "facts": [
   {"id": "f1", "text": "Interim accounts 30 June 2024: half-year profit CHF 19.3m; audit pending", "source": "dms://finance/2024/interim-accounts-h1.md", "as_of": "2024-06-30"},
   {"id": "f2", "text": "Hollis provision unchanged at CHF 22m", "source": "dms://legal/claims/2022-031-hollis.md", "as_of": "2024-08-22"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "675a"}, "paragraph": "1",
    "proposition": "The general meeting may resolve an interim dividend on the basis of interim accounts."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "675a"}, "paragraph": "2",
    "proposition": "The interim accounts must be audited before the general meeting resolves, unless an exception applies."},
  ],
  "commitments": [
   {"id": "c1", "text": "Distributions only if pro-forma leverage does not exceed 2.50x (clause 23.4 of the 2024 facility)", "source": FACILITY_2024 + "#23.4", "test_date": "2024-09-26"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "Is the Hollis claim taken into account?", "answer": "Provision unchanged at CHF 22m; no new development."},
  ],
  "review": {"date": "2024-12-31", "reopen_if": ["l1 or l2 amended", "f1 audit qualified or not delivered", "f2 claim amended or provision raised", "c1 test failed"]},
 },
 {
  "id": "rec_18_liquidity_monitoring_2024", "type": "board.capital_loss", "decided_at": "2024-11-21", "decided_by": BOARD,
  "minutes": "dms://board/2024-11-21_minutes.md#4",
  "text": "Monthly 13-week liquidity planning to the board, a freeze of non-essential capital expenditure until the 31 December 2024 covenant test, and a precautionary waiver negotiation with the lenders.",
  "purpose": "Keep the company solvent and within the facility's leverage covenant despite higher claim provisions and lower EBITDA.",
  "owner": CFO, "fallback_owner": ACC,
  "restrictions": ["No non-essential capital expenditure until the covenant test of 31 December 2024"],
  "facts": [
   {"id": "f1", "text": "Forecast leverage 2.9x at 31 December 2024 (covenant 3.0x); EBITDA forecast CHF 61m", "source": "dms://finance/2024/liquidity-forecast-2024-11.md", "as_of": "2024-11-15"},
   {"id": "f2", "text": "Brunnmatt claim CHF 14m, provision CHF 6m", "source": "dms://legal/claims/2024-008-brunnmatt.md", "as_of": "2024-11-08"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "725"}, "paragraph": "1",
    "proposition": "The board monitors the company's solvency."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "725"}, "paragraph": "2",
    "proposition": "If insolvency threatens, the board takes measures to secure solvency and, if needed, further restructuring measures."},
   {"id": "l3", "get_law": {"abbreviation": "OR", "article": "716a"}, "paragraph": "1",
    "proposition": "Financial planning, as far as needed to manage the company, is a non-transferable duty of the board."},
  ],
  "commitments": [
   {"id": "c1", "text": "Leverage covenant of 3.0x net debt to EBITDA (clause 22.1 of the 2024 facility)", "source": FACILITY_2024 + "#22.1", "test_date": "2024-12-31"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "A covenant breach would make the whole financing callable; a waiver should be negotiated now.", "answer": "Talks with Bank Vorderland AG under way; decision in January 2025."},
  ],
  "review": {"date": "2025-03-31", "reopen_if": ["l1, l2 or l3 amended", "f1 forecast leverage above 3.0x", "f2 claim amended", "c1 test failed"]},
 },
 {
  "id": "rec_19_wyss_advisory_mandate", "type": "board.conflict_transaction", "decided_at": "2025-02-20", "decided_by": BOARD + " (members without conflict)",
  "minutes": "dms://board/2025-02-20_minutes.md#3",
  "text": "Twelve-month AI architecture mandate (up to CHF 480,000 at CHF 290 an hour) to Wyss Advisory GmbH, controlled by director Dr. Andreas Wyss, the cheapest of three offers.",
  "purpose": "Obtain AI architecture advice at the best price while protecting the company from the director's conflict of interest.",
  "owner": GC, "fallback_owner": CHAIR,
  "restrictions": ["Dr. Wyss does not invoice work personally", "Invoices approved by the Chair of the Audit Committee", "Fee cap CHF 480,000"],
  "facts": [
   {"id": "f1", "text": "Three offers: CHF 290, 310 and 345 an hour", "source": "dms://related-party/wyss-advisory-offers-2025.md", "as_of": "2025-02-10"},
   {"id": "f2", "text": "Contract of 3 March 2025 with fee cap and invoice approval by the audit committee chair", "source": "dms://related-party/wyss-advisory-agreement-2025.md", "as_of": "2025-03-03"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "1",
    "proposition": "Directors and managers must inform the board without delay of conflicts of interest affecting them."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "2",
    "proposition": "The board must take the measures needed to safeguard the company's interests in case of a conflict of interest."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_139_III_24", "pinpoint": "3.2"},
    "proposition": "The duty of loyalty requires directors to align their conduct with the interest of the company."},
   {"id": "l4", "cite": {"decision_id": "bge_BGE_130_III_213", "pinpoint": "2.1"},
    "proposition": "The duty of loyalty of governing officers is determined by company law and, where they are employees, also by employment law."},
  ],
  "commitments": [
   {"id": "c1", "text": "Fee cap of CHF 480,000 over twelve months", "source": "dms://related-party/wyss-advisory-agreement-2025.md", "test_date": "2026-03-02"},
  ],
  "objections": [],
  "review": {"date": "2026-02-28", "reopen_if": ["l1 or l2 amended", "l3 or l4 qualified", "fee cap exceeded", "Dr. Wyss invoices personally"]},
 },
 {
  "id": "rec_20_md200_ai_visual_inspection", "type": "board.ai_deployment", "decided_at": "2025-03-20", "decided_by": BOARD,
  "minutes": "dms://board/2025-03-20_minutes.md#5",
  "text": "Replacement of manual visual final inspection of MD-200 gearbox housings by an image-recognition model from 1 July 2025, after three months of parallel operation and with a 5% manual sample.",
  "purpose": "Detect more defects in a medical device component while keeping the quality system compliant.",
  "owner": RA, "fallback_owner": CEO,
  "restrictions": ["Only for the MD-200 line", "5% manual sample", "Three months of parallel operation before go-live"],
  "facts": [
   {"id": "f1", "text": "Validation: detection 99.2% (manual 97.5%), false alarms 1.8%", "source": "dms://ai/md-visual-inspection-validation-2025.md", "as_of": "2025-03-10"},
   {"id": "f2", "text": "MD-200 is a class I device with no notified body involved", "source": "dms://products/md-200/declaration-of-conformity-2021.md", "as_of": "2021-05-20"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "MepV", "article": "6"}, "paragraph": "2",
    "proposition": "Devices must meet the general safety and performance requirements of Annex I EU-MDR."},
   {"id": "l2", "get_law": {"abbreviation": "MepV", "article": "46"}, "paragraph": "1",
    "proposition": "When placing a device on the market, the manufacturer ensures that it was designed and manufactured in accordance with the ordinance, which covers changes to the final inspection."},
   {"id": "l3", "foreign": {"jurisdiction": "EU", "source": "dms://ai/md-visual-inspection-validation-2025.md",
                            "description": "EU regulation on artificial intelligence: high-risk classification of AI used as a safety component of a medical device, as assessed by EU counsel"},
    "proposition": "The inspection model is not a high-risk AI system while the MD-200 remains a class I device without notified-body involvement."},
  ],
  "commitments": [],
  "objections": [
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "A parallel phase is needed.", "answer": "Accepted; three months of parallel operation."},
  ],
  "review": {"date": "2026-03-31", "reopen_if": ["l1 or l2 amended", "l3 EU classification or dates change", "f2 MD-200 classification changes", "detection rate below manual rate"]},
 },
 {
  "id": "rec_21_md200_us_launch", "type": "product.market_approval", "decided_at": "2025-06-26", "decided_by": PRC + " and Board of Sernfeld Drives Inc.",
  "minutes": "dms://products/md-200/us-launch-decision-2025.md",
  "text": "Sale of the MD-200 in the United States by Sernfeld Drives Inc. from 1 August 2025 as a class I device exempt from premarket notification.",
  "purpose": "Enter the US patient-lift market through the US subsidiary under general controls only.",
  "owner": US_COUNSEL, "fallback_owner": GC,
  "restrictions": ["Sale in the United States only, by Sernfeld Drives Inc.; not in Canada or Mexico", "No claims of use in ambulance stretchers"],
  "facts": [
   {"id": "f1", "text": "US counsel memo: class I, exempt from premarket notification, subject to general controls", "source": "dms://products/md-200/us-regulatory-memo-2025.md", "as_of": "2025-06-02"},
  ],
  "law": [
   {"id": "l1", "foreign": {"jurisdiction": "US", "source": "dms://products/md-200/us-regulatory-memo-2025.md",
                            "description": "US federal device classification and premarket-notification exemption for powered patient-lift components, per US counsel memo"},
    "proposition": "The MD-200 is a class I device exempt from premarket notification in the United States."},
   {"id": "l2", "get_law": {"abbreviation": "MepV", "article": "46"}, "paragraph": "1",
    "proposition": "The Swiss manufacturer remains responsible under the Swiss rules for the design and manufacture of the device it also sells in the United States."},
  ],
  "commitments": [],
  "objections": [],
  "review": {"date": "2026-06-30", "reopen_if": ["l1 classification or exemption changes", "l2 amended", "sale in Canada or Mexico", "claims of use in ambulance stretchers"]},
 },
 {
  "id": "rec_22_service_gmbh_overindebtedness_2025", "type": "board.capital_loss", "decided_at": "2025-10-16", "decided_by": BOARD + " (as sole shareholder and creditor of Sernfeld Service GmbH)",
  "minutes": "dms://board/2025-10-16_minutes.md#2",
  "text": "Confirmation and extension of the CHF 6.0m subordination to cover interest, audit of the interim accounts by 31 October 2025 and a restructuring plan, with a merger decision if no anchor customer signs by 31 January 2026.",
  "purpose": "Avoid notifying the court for Sernfeld Service GmbH (over-indebted by CHF 2.6m at going-concern values) while restructuring it.",
  "owner": CFO, "fallback_owner": GC,
  "restrictions": ["Merger with the parent to be proposed if no anchor customer signs by 31 January 2026"],
  "facts": [
   {"id": "f1", "text": "Interim accounts 31 August 2025: over-indebtedness CHF 2.6m at going-concern values, CHF 4.1m at liquidation values; accrued interest CHF 0.31m", "source": "dms://finance/service-gmbh/interim-accounts-2025-08-31.md", "as_of": "2025-08-31"},
   {"id": "f2", "text": "Subordination extended to the loan of CHF 6.0m and all interest for the duration of the over-indebtedness", "source": "dms://finance/service-gmbh/subordination-amendment-2025.md", "as_of": "2025-10-17"},
   {"id": "f3", "text": "Restructuring plan relies on an anchor customer signing by 31 January 2026", "source": "dms://finance/service-gmbh/restructuring-plan-2025.md", "as_of": "2025-10-10"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "725b"}, "paragraph": "3",
    "proposition": "If the interim accounts show over-indebtedness, the board must notify the court."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "725b"}, "paragraph": "4",
    "proposition": "No notification is needed if creditors subordinate claims, including interest, in the amount of the over-indebtedness, or if there is a reasonable prospect of removing it within 90 days of the audited interim accounts."},
   {"id": "l3", "get_law": {"abbreviation": "OR", "article": "820"}, "paragraph": None,
    "proposition": "The company-law rules on capital loss and over-indebtedness apply to a GmbH."},
   {"id": "l4", "cite": {"decision_id": "bge_BGE_150_III_315", "pinpoint": "4.2"},
    "proposition": "Without a notification of over-indebtedness by the board, the auditor or the licensed auditor, bankruptcy cannot be opened on that basis."},
  ],
  "commitments": [
   {"id": "c1", "text": "Audit of the interim accounts by 31 October 2025", "source": "dms://board/2025-10-16_minutes.md#2", "test_date": "2025-10-31"},
   {"id": "c2", "text": "Anchor customer contract by 31 January 2026, otherwise merger proposal", "source": "dms://finance/service-gmbh/restructuring-plan-2025.md", "test_date": "2026-01-31"},
  ],
  "objections": [
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "The plan depends on a new anchor customer; without it the subordination alone is not a restructuring.", "answer": "Merger with the parent will be proposed if no anchor customer signs by 31 January 2026."},
  ],
  "review": {"date": "2026-01-31", "reopen_if": ["l1, l2 or l3 amended", "l4 qualified", "f1 over-indebtedness exceeds the subordinated amount", "c2 no anchor customer by 31 January 2026"]},
 },
 {
  "id": "rec_23_dividend_fy2025", "type": "board.dividend", "decided_at": "2026-03-12", "decided_by": BOARD,
  "minutes": "dms://board/2026-03-12_minutes.md#4",
  "text": "Proposal to the AGM of an ordinary dividend of CHF 4.20 per share (CHF 8.4m) for financial year 2025, payable after the auditor's confirmation.",
  "purpose": "Distribute free reserves while keeping liquidity for the 2027 refinancing.",
  "owner": GC, "fallback_owner": ACC,
  "restrictions": ["Payment only after the auditor's confirmation of the proposal"],
  "facts": [
   {"id": "f1", "text": "Free reserves of CHF 310m per audited 2025 accounts", "source": "dms://finance/2025/audited-accounts-extract.md", "as_of": "2025-12-31"},
   {"id": "f2", "text": "Pending Hollis product-liability claim, provision CHF 40m; trial set for 9 November 2026", "source": "dms://legal/claims/2022-031-hollis.md", "as_of": "2026-03-01"},
   {"id": "f3", "text": "Leverage 2.10x at 31 December 2025; pro-forma 2.21x after the dividend", "source": "dms://treasury/compliance-certificate-2025-12-31.md", "as_of": "2025-12-31"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "2",
    "proposition": "Dividends may be paid only out of the balance-sheet profit and out of reserves created for this purpose."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "675"}, "paragraph": "3",
    "proposition": "Dividends may be set only after the allocations to the statutory and voluntary retained earnings."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_140_III_533", "pinpoint": "3"},
    "proposition": "Shareholders who receive an unjustified distribution can be required to return it, in addition to the directors' liability."},
  ],
  "commitments": [
   {"id": "c1", "text": "Net-debt covenant of 3.0x EBITDA in the 2024 facility (clause 22.1)", "source": FACILITY_2024 + "#22.1", "test_date": "2026-06-30"},
   {"id": "c2", "text": "Distributions only if pro-forma leverage does not exceed 2.50x (clause 23.4)", "source": FACILITY_2024 + "#23.4", "test_date": "2026-05-07"},
  ],
  "objections": [
   {"by": "Annika Zwahlen (CFO)", "text": "Claim f2 could exceed the provision.", "answer": "Provision reviewed by external counsel; reassess if the claim is amended."},
  ],
  "review": {"date": "2026-09-30", "reopen_if": ["l1 or l2 amended", "l3 qualified or overruled", "f2 amended or provision raised", "c1 test failed", "c2 test failed"]},
 },
 {
  "id": "rec_24_kaelin_supply_renewal_2026", "type": "board.conflict_transaction", "decided_at": "2026-05-14", "decided_by": BOARD + " (members without conflict)",
  "minutes": "dms://board/2026-05-14_minutes.md#3",
  "text": "Renewal of the castings supply framework agreement with Kälin Giesserei AG for 1 July 2026 to 30 June 2029 with an aluminium price index, annual benchmark and a termination right above 5% over market.",
  "purpose": "Keep a short-lead-time castings supply at near-market prices while protecting the company from the director's conflict of interest.",
  "owner": GC, "fallback_owner": CHAIR,
  "restrictions": ["Annual price comparison", "Termination right if Kälin is more than 5% above the market mean", "Peter Kälin does not sign for the company"],
  "facts": [
   {"id": "f1", "text": "2026 benchmark: Kälin +1.5% over the mean of three suppliers, lead time 4 weeks against 7–10", "source": "dms://contracts/kaelin-benchmark-2026.md", "as_of": "2026-05-04"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "1",
    "proposition": "Directors and managers must inform the board without delay of conflicts of interest affecting them."},
   {"id": "l2", "get_law": {"abbreviation": "OR", "article": "717a"}, "paragraph": "2",
    "proposition": "The board must take the measures needed to safeguard the company's interests in case of a conflict of interest."},
   {"id": "l3", "cite": {"decision_id": "bge_BGE_127_III_332", "pinpoint": "2"},
    "proposition": "A self-dealing transaction of a director can be approved by another director with signing authority."},
  ],
  "commitments": [
   {"id": "c1", "text": "Annual benchmark; termination right above 5% over market (clause 7.3)", "source": "dms://contracts/kaelin-supply-agreement-2026.md#7.3", "test_date": "2027-05-31"},
  ],
  "objections": [
   {"by": "Sophie Marchand (board member)", "text": "Are there alternatives?", "answer": "A second supplier for 30% of the volume is being built up."},
  ],
  "review": {"date": "2027-06-30", "reopen_if": ["l1 or l2 amended", "l3 qualified", "c1 benchmark above 5% over market"]},
 },
 {
  "id": "rec_25_board_desk_agent", "type": "board.ai_deployment", "decided_at": "2026-06-18", "decided_by": BOARD,
  "minutes": "dms://board/2026-06-18_minutes.md#4",
  "text": "Introduction from 1 September 2026 of 'Board Desk', an AI agent that summarises board papers and drafts minutes, excluding agenda items with conflicts of interest, with every draft checked by the secretary.",
  "purpose": "Reduce the secretary's drafting work while keeping board information confidential and in Switzerland.",
  "owner": CDO, "fallback_owner": GC,
  "restrictions": ["No processing of agenda items involving a conflict of interest", "Drafts never sent automatically; checked by the secretary", "Storage in Switzerland"],
  "facts": [
   {"id": "f1", "text": "Provider BoardFlow AG, Swiss cloud region, no storage by the model provider, deletion after 90 days", "source": "dms://ai/board-desk-risk-assessment-2026.md", "as_of": "2026-06-05"},
  ],
  "law": [
   {"id": "l1", "get_law": {"abbreviation": "OR", "article": "717"}, "paragraph": "1",
    "proposition": "Directors must perform their duties with all due diligence and safeguard the company's interests in good faith, which includes confidentiality."},
   {"id": "l2", "get_law": {"abbreviation": "DSG", "article": "9"}, "paragraph": "1",
    "proposition": "Processing may be entrusted to a processor by contract if the processor processes the data only as the controller itself may."},
   {"id": "l3", "get_law": {"abbreviation": "DSG", "article": "16"}, "paragraph": "1",
    "proposition": "Personal data may be disclosed abroad if the Federal Council has found that the recipient state ensures adequate protection."},
   {"id": "l4", "get_law": {"abbreviation": "DSG", "article": "22"}, "paragraph": "1",
    "proposition": "A data protection impact assessment is required where processing may entail a high risk to the personality or fundamental rights of data subjects."},
  ],
  "commitments": [],
  "objections": [
   {"by": "Prof. Dr. Thomas Bärtschi (board member)", "text": "Papers on conflicts of interest must not be processed.", "answer": "Accepted as a restriction."},
  ],
  "review": {"date": "2027-03-31", "reopen_if": ["l1 to l4 amended", "f1 provider stores data outside Switzerland", "conflict items processed", "drafts sent automatically"]},
 },
]
