# Governance and Removal Policy

OpenCaseLaw republishes only decisions that courts or public bodies have already published. The originating source remains the authoritative record for publication status, anonymization state, and corrections.

## Scope

- **Code** is licensed under MIT.
- **Dataset packaging and added metadata** are released under CC0-1.0, to the extent any rights exist in those additions.
- **Underlying decision texts** remain official published decisions sourced from the originating courts or public bodies.

## Source-of-truth principle

If there is any conflict between an OpenCaseLaw copy and the source publication, the source publication controls. OpenCaseLaw aims to mirror the source, not supersede it.

## Source withdrawals and re-anonymization

If a court or public body removes, replaces, or re-anonymizes a published decision at the source:

- future scrapes should reflect that change
- downstream build artifacts may be regenerated to remove or update the affected record
- mirrors and derived exports may lag briefly behind the source while the next pipeline run or manual update completes

For Federal Supreme Court decisions, a daily check re-fetches recently published decisions at search.bger.ch and lists those the court has withdrawn for at least ten days as candidates for review. The check does not remove anything; de-listing, removal or re-sync remain maintainer decisions under the review standard below.

## Correction and removal requests

Verified requests may be sent to `team@jonashertner.com` or filed via the project issue tracker when public discussion is appropriate.

Requests should include, where possible:

- the docket number or `decision_id`
- the source URL
- the requested action: correction, re-sync with source, de-listing, or removal
- the reason for the request
- evidence that the requester is the originating court/public body, an authorized representative, or an affected person with a concrete privacy or publication concern

## Review standard

Maintainers review requests case by case. Relevant factors include:

- whether the source decision is still publicly available
- whether the source has changed the anonymization state or withdrawn the publication
- whether OpenCaseLaw metadata or packaging introduced an independent error
- whether continued republication creates a material privacy or safety concern that outweighs the value of continued indexing

## Typical outcomes

Depending on the facts, maintainers may:

- update metadata or links
- re-sync the record to the current source version
- de-list the decision from public artifacts
- remove the decision from future releases
- decline the request when the source publication is unchanged and no independent OpenCaseLaw error is shown

## Best-effort timing

OpenCaseLaw is maintained on a best-effort basis. Urgent requests should say why urgency matters. Removal from derived artifacts may require a rebuild cycle rather than an immediate edit in place.
