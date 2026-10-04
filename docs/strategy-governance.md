# Strategy Governance

Policy version: `STRATEGY-GOVERNANCE-20260911`.

This is the shared strategy-review contract adopted by the private rulebook,
goal and warmup prompts. Public templates are examples, not live account
instructions. Current named user decisions and stricter safety constraints
remain binding. A conflict requires a scoped review, not an automatic change
to a holding, order, registry, target, or historical record.

**Status: agent-review policy adopted; additional machine enforcement pending.**
This document does not implement a new MCP feature, change an authorization
gate, or authorize live or paper trades. Existing validators remain required.
A passing validator cannot certify checks that it does not implement.

## Mandate And Books

Seek short- and medium-term returns with explicitly budgeted downside, full
trading costs, and intentional exposure. More trades, more invested cash, and
realized profits alone are not evidence of a better strategy. High risk does
not imply a positive expected return or guarantee profit.

Classify each campaign before recommending a target or protection change:

| Book | Review horizon | Required rationale |
|---|---|---|
| Strategic core | Normally 3-12 months, or a deliberately approved longer horizon | Durable thesis, absolute retained floor, invalidation and next thesis review |
| Tactical participation | Normally 2-12 weeks | Observable setup, exit/invalidation, friction-adjusted opportunity and time stop |
| Event or special situation | Days to about 6 weeks | Named event, publication checkpoint, gap-risk budget and post-event decision |

These are classification guides, not automatic expiry or forced-sale rules.
Do not silently convert a legacy long-term core into a short-term trade or
extend a failed tactical setup into an indefinite recovery hold. Record the
evidence and explicit decision for any horizon or target change.

The user's maximum tolerable portfolio drawdown and strategic cash/reserve
mandate remain `UNSET` unless explicitly recorded. Do not invent them or infer
them from current cash. Existing per-trade risk, aggregate risk, factor,
concentration, headroom, friction and churn controls remain unchanged. Missing
mandate choices do not excuse stopping read-only analysis of other scopes.

## Four Independent Results

Every material account/campaign review must distinguish:

| Axis | What it proves | What it does not prove |
|---|---|---|
| Accounting integrity | Exact sources, lots, allocations, current holdings and order parity | A sound investment decision or filled recovery |
| Decision freshness | Current thesis, setup, event evidence, target and dated next gate | An executable or accepted order |
| Execution lifecycle | Verified state of each intent, submission, order and fill | Adequate total exposure or favorable performance |
| Economic exposure | Actual holdings versus the approved target and unresolved disposition debt | Safe investment, profit, or permission to trade |

Do not collapse these into a single green status. A valid dormant review
ladder can satisfy the existing *governed planning* test while execution is
`NOT_PLACED` and exposure remains `UNDERWEIGHT`. An accepted active order
is `AWAITING_TRIGGER` or `AWAITING_FILL`, never restored exposure.

## Broker SELL Protection Is A Separate Result

Protection-classification completeness is not broker SELL protection. For each
account, report active SELL row count plus counts of material stop-eligible
positions, positions with active broker SELL coverage, and positions requiring
protection review. For each orderbook, report exact held `Antal`, actual
protected `Antal`, coverage state, and every active SELL row's exact `Antal`.
Never sum `Antal` across unlike instruments as coverage proof. Registry
`governance_complete`, `protection_complete`, `VALID`, or
`stale_plan_count=0` output proves only the checks it implements. A protection
exception never makes a position broker-covered or contributes protected
`Antal` for its orderbook.

If an account has zero active SELL rows while any material, stop-eligible
orderbook has held `Antal > 1`, record
`BROKER_SELL_PROTECTION_ABSENT / PROTECTION_REVIEW_REQUIRED`. This blocks
clean, fixed, protected, and complete claims even if every position has a valid
classification. An empty or default `CURRENT_ACTIVE_SELL_BASELINE` helper
result is `NOT_ASSESSED` for strategic SELL coverage, not evidence of
protection or of no gap.

A no-stop core exception requires current instrument-specific evidence,
`decision_at`, `evidence_as_of`, `next_review_at`, `valid_until`, a stated
gap-risk decision, and an explicit choice among protecting an exact
tactical/profit slice, using a wider calibrated core row, or deliberately
leaving the core unprotected. Missing, generic, or elapsed evidence is
`PROTECTION_REVIEW_REQUIRED`. The review requirement does not infer a SELL
quantity or authorize blanket stops. Campaign floors, cumulative sale allowances,
the exposure-debt brake, exact live approval, and named-asset rules remain
binding.

## Meaningful Exposure

Review every position below SEK 25,000 for economic materiality. Preserve the
existing below-SEK-20,000 structural gate; explicitly include the SEK
20,000-25,000 borderline band in the agent review until software catches up.
These are review thresholds, not minimum purchases or universal target sizes.
Small positions still carry real risk; a single expensive unit can be material.

For desired growth or rebuilding, name an approved target in Antal or a SEK
band, actual exposure, the shortfall, and one evidenced next action or dated
wait/avoid decision. If no target is approved, report `TARGET_UNSET` rather
than declaring the holding adequate or inventing a target from available cash.
Use risk-at-invalidation, liquidity, concentration, conviction, horizon and
full friction to propose a meaningful size, not a generic amount.

A funded plan, valid exception, waiting gate, accepted order or explanation
for cash **does not close an economic shortfall**. Actual fills may reduce it;
an explicit target reduction may resolve the revised allocation decision but
must retain the original target, rationale and unresolved history. A named
rotation restores destination exposure only after its fills and an explicit
source-target disposition; it is not a same-instrument buyback.

Keep two different measurements:

- **Sale-attributed recovery Antal:** exact same-account, same-instrument lots,
  qualifying fills and terminal dispositions. Active allocated orders can
  reserve lot coverage in the existing accounting equation but do not reduce
  the actual unfilled recovery obligation. Never subtract one ticker's units
  from another's, or net one account's fills against another's obligation.
- **Capital-disposition debt in SEK:** exact sale-proceeds allocations less
  evidenced deployed capital or explicit release/reallocation decisions, with
  dated FX and costs. Prevent source over-allocation across recovery and
  rotation. Track any retained cash surplus explicitly. Do not add unlike
  share quantities into an account or portfolio debt number.

Mark-to-market underweight is a separate target-band measurement. Market/FX
moves are not SELL-created debt or BUY fills. An above-sale recovery may close
the Antal obligation while costing more; show the incremental capital and
remaining target gap separately. Terminal decisions retain all existing exact
lot, contradiction, expiry and revalidation requirements.

## Prevent Repeated Core Erosion

Each reviewed campaign needs a stable identity, approved baseline quantity,
target/band, **absolute core Antal floor**, cumulative tactical-sale allowance,
attributed sales and recoveries, and next review. These are required review
facts, not claims that new registry fields already exist.

Derive the existing 75% quality-core or 50% high-beta retained-floor convention
from the approved campaign baseline, rounding the retained floor upward to
whole units. Do not recalculate it downward from the shrinking post-sale
holding. Existing smaller named floors require their own explicit decision;
do not retrospectively invent a baseline or floor for legacy rows.

Aggregate every possible sequential SELL fill across regular orders and stops.
Respect the absolute floor, mechanical holdings ceiling, and remaining
cumulative sale allowance simultaneously. Broker rows are not assumed OCO.
Two one-third SELL slices cannot implicitly override a 50% retained floor.
There is no generic half/third tranche split. A fill does not replenish the
cumulative sale allowance; a new campaign decision and the five-session cycle
brake are required before another substantially similar harvest.

If the campaign baseline is unproved, flag `CAMPAIGN_BASELINE_UNSET` and do
not call an optional harvest ready. Preserve existing orders pending exact
review; this policy does not cancel protection or force a new core purchase.
Recovered shares default to core/hold. Assess new fills promptly, but protect
only the exact slice whose current strategy calls for a stop.

Pause further optional intact-thesis harvesting while prior restoration or
disposition debt grows or remains stranded. Require a funded recovery/rotation
or explicit target-reduction decision before an optional harvest is ready.
**Risk-critical exits must not wait for a buyback plan.** They still require
all applicable authority and broker safety gates, with disposition documented
without forcing re-entry into a broken thesis.

## Recovery Purpose And Actual Trigger

Classify each entry as `PULLBACK`, `CONTINUATION`, `REVERSAL`, or
`TAIL_RESIDUAL`; name its reference, supported mechanism, exact quantity, hard
cap, invalidation, event gate, expiry and full-friction rationale. One to three
stock-specific stages may be used when quantities are economically material.
Do not copy percentage vectors, default quantity fractions or trailing margins
between instruments.

The sale reference is not an automatic price ceiling. A bounded continuation
above it can be evaluated when current thesis, structure, liquidity, risk,
capacity and friction support it. A lower price is not itself a valid setup.
Do not chase a rebound to conceal a missed crossing.

Routine buyback summaries use percentages and clearly named references:
`drop below sale`, `premium above sale`, or `rebound from a trailing low`.
Do not expose raw prices, triggers, child limits or monetary values outside
the binding private exact-authorization/readback process. Preserve positive
drop percentages for actual pullback stages. The existing ladder schema does
not represent above-sale continuation: label its unsupported representation
`MACHINE_ENFORCEMENT_PENDING`; never forge a below-sale percentage to pass.

`LADDER_DORMANT` means a review plan **not placed at the broker**. An actual
conditional order is active even if far away. Report the precise broker
trigger separately from the analytical promotion gate: a price-only order
does not wait for earnings, volume or thesis confirmation merely because its
comment says so. Such a mismatch is a strategy issue, not harmless dormancy.

Deep tail rows consume risk/capacity and are not practical participation by
themselves. If a nearer entry is unjustified, preserve the exact shortfall,
maximum path drawdown and next evidence gate; do not force a trade. Existing
reachability, multi-lot parity and missed-crossing controls remain binding.

## SELL/BUY Ordering Risk

A lower fixed BUY trigger **does not guarantee** that the SELL has filled. A
SELL stop-limit can remain unfilled through a gap while a BUY fills. A trailing
BUY can also add exposure before the sale. Price separation is not OCO or
fill-contingent execution.

The stop-limit non-execution risk is documented in
[FINRA's order-risk guidance](https://www.finra.org/rules-guidance/notices/16-19)
and the [SEC investor bulletin on order types](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-14).
The independent SELL/BUY ordering conclusion is the strategy audit's inference
from that risk, not a claim that either source certifies this MCP's behavior.

Before any independently active pre-sale BUY is considered ready, count its
full possible fill alongside the entire unsold holding and all other
commitments. It must pass cash, factor, invalidation-risk, duplicate and
full-friction gates **without assumed SELL proceeds**. Label it independent
pre-sale inventory and do not allocate it to an unexecuted sale.

Otherwise wait for an authenticated SELL fill and review recovery from the
actual transaction. Disclose latency, authorization expiry, session, halt and
rebound risk. Claim broker-enforced fill dependency only if current MCP
capabilities, preview and readback explicitly prove it. Neither a prompt nor
an arbitrary 5-6% dead zone creates that dependency.

## Freshness And Lifecycle

Every actionable decision needs `decision_at`, `evidence_as_of`,
`next_review_at`, `valid_until`, source IDs, exact scope, materiality and a
`supersedes` link when replacing a decision. Set evidence-specific deadlines
from the next event, tradable session, setup and order expiry; a far-future
broker expiry is not a fresh strategy. Missing deadlines are an explicit
freshness gap. Never renew a terminal decision from its scheduler label.

The current runtime's `stale_plan_count` is not proof that elapsed review
deadlines, source age or semantic contradictions were checked. Perform those
checks explicitly and retain the machine-enforcement gap.

Track each intent through `DRAFT`, `VALIDATED`, `SUBMITTED`, `ACCEPTED`,
`PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `EXPIRED` or `CANCELLED`, as evidenced.
Use `UNKNOWN` for ambiguous submission/readback and never retry blindly.
Partial fills remain accounted for after cancellation or expiry. Preview
success is not acceptance; acceptance is not a fill. Journal synchronous
rejections even when no broker ID or raw failed-order row exists, preserving
the intent, scope, time and safe error category without credentials.

Use native MCP only for broker, registry and operational monitoring actions.
Offline artifacts may summarize evidence but cannot submit, emulate a bridge,
or provide a parallel execution channel. Keep exact authorization, scoped
preflight/postflight, asset-specific restrictions and final authorization-off
verification unchanged. Missing enforcement is not authority to bypass a gate.

## Monitoring And Outcomes

Existing automation cadence is unchanged. Twice-daily review is **not
continuous live monitoring**. Record last successful refresh per scope,
source time, session state, missed transitions and the next actual scheduled
check. Notify on material change, failure or required user action, not
unchanged routine status.

Recommended architecture, **not scheduled or implemented by this policy**:
venue warmup; fill/error/expiry events or bounded lifecycle polling; changed-row
opportunity review; catalyst checkpoints; close reconciliation; weekly full
history and outcome review. Reduced-scope checks must never satisfy the full
review/streak requirements. Until a separately approved incremental boundary
is implemented, scheduled full reviews retain every existing mandatory gate.

Keep account-wide capacity/authority failures distinct from instrument/lot
evidence failures. Continue read-only research on unaffected scopes instead
of stalling the entire review; do not weaken current mutation gates. Preserve
all due and overdue scheduler items and distinguish them from valid future
waiting rows.

The outcome scorecard should use aligned windows and evidenced external cash
flows: total return, matched-risk/frozen-holdings comparison where history is
complete, drawdown, turnover, all observed/modeled costs, time below target,
filled recovery, and post-decision adverse/favorable movement. Label models
and unavailable inputs. Cash generated by selling is not profit; realized
profit is not total return; order distance is not fill probability.

Validate proposed strategy changes with a timestamped **decision-only shadow
log**, not automatic paper or live orders. Do not fabricate a backtest, tune
targets to hide missed moves, or claim success from a governance streak alone.

## Implementation Backlog

| Audit finding | Adopted review requirement | Machine work still needed |
|---|---|---|
| F1, F10 | Four axes; dimensionally correct debt and outcome scorecard | Typed economic state, target/debt calculation and outcome gates |
| F2 | Typed entry purpose and labelled percentage reference | Signed continuation representation and mirror/validator tests |
| F3 | Absolute campaign floor and cumulative sale allowance | Campaign identity/baseline migration and sequential exposure validation |
| F4 | Independent pre-sale BUY stress, or confirmed-fill-first review | Explicit dependency capability check and worst-case regression tests |
| F5 | Dated source, decision and expiry checks | Semantic/time freshness instead of ID-only stale detection |
| F6 | Exact lifecycle including no-ID rejection and ambiguous state | Unified durable lifecycle ingestion and idempotent reconciliation |
| F7 | Separate dormant plan, active trigger and practical participation | Runtime/artifact consistency and trigger-versus-gate checks |
| F8, F9 | Explicit books, deadlines and honest monitoring coverage | Reviewed migration, incremental monitoring and scheduler consolidation |
| Zero-SELL false green | Separate classification completeness from account-level position counts and per-orderbook held/protected `Antal`; fail material H>1 no-stop rows to review | Implemented in contract `2026-09-12.position-protection-v2`: closed current/expiring no-stop evidence, exact calibrated target plus retained-core Antal, token-SELL/under/overcoverage rejection, and dated `NON_STOP_ELIGIBLE` capability evidence. Metadata never creates broker coverage; the active MCP bridge must load the changed runtime before its readback is authoritative. |

Only the zero-SELL typed-evidence and exact-target slice above is implemented.
Items still described as pending remain open until separately approved
implementation and tests prove them. The implemented zero-SELL gate does not
itself migrate live registries, create broker protection, or repair the
underlying portfolio gaps. Do not migrate live registries from this document
alone or relax a validator to manufacture completion.
