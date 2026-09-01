# Staged evidence protocol for long-running AI work

## Decision

Expensive source-bound evidence must run as a deterministic workflow, not as a side effect of
an open-ended fixer/reviewer conversation. The repository therefore separates code convergence
from evidence execution:

```text
implementation -> checks -> complete code review -> CODE_APPROVED freeze
    -> bounded evidence generation -> final checks -> final evidence review
```

One complete source-repair/refreeze/rerun is permitted after the first final review. A third
generation is a terminal workflow failure, not another automatically invented version.
Before evidence, one complete all-findings code repair is permitted and exactly one subsequent
verification review is reserved. A failed verification stops without launching an unreviewable
fixer.

## Why this design

The previous continuation created 38 Phase 3 registration versions because each fixer could
freeze evidence before the next independent reviewer discovered another source defect. The
append-only rule correctly prevented silent rewriting, but it exposed a scheduling error: code
convergence and expensive evidence production were interleaved.

The replacement follows four primary-source principles:

- Anthropic recommends evaluator-optimizer loops when criteria are clear and refinement is
  measurably useful, while keeping deterministic workflows distinct from autonomous agents:
  [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).
- Anthropic's long-running-agent harness uses a structured progress ledger and requires
  self-verification before work is marked passing:
  [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).
- OpenAI recommends incremental orchestration, explicit exit conditions, guardrails and evals
  rather than starting with unnecessary autonomous complexity:
  [A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/).
- LangGraph's durable-execution guidance requires deterministic replay, checkpointed side
  effects and idempotent retry behavior; it also recommends draining in-flight runs before
  changing their code:
  [Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api),
  [backward compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility).
- AutoGen exposes stateful termination conditions and allows limits to be combined, reinforcing
  that approval, iteration, time and resource bounds belong in code rather than prompt prose:
  [Termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html).

No source establishes an “absolute best” architecture for every agent system. This protocol is
the smallest design that directly addresses this repository's observed failure: repeated source
drift invalidating costly scientific evidence.

## Enforced invariants

1. Code-stage actors and deterministic code checks cannot create, overwrite or remove any
   configured evidence artifact.
2. The code reviewer receives the whole defect set and is explicitly asked not to defer known
   findings. The fixer must closure-audit adjacent trust-boundary bypasses before the reserved
   verification review.
3. `CODE_APPROVED` is represented by a persisted SHA-256 manifest over configured protected
   sources, not by an informal message.
4. Evidence execution checks that manifest and a SHA-256-bound artifact budget at start, every
   progress heartbeat, after final checks and during final review. Drift, overwrite or deletion
   terminates and reaps the owned process tree.
5. Artifact budgets are measured relative to the run's initial inventory and separately per
   evidence series; registrations, outcomes and supporting evidence are all covered.
6. An interrupted generation remains in progress and resumes with the same generation number.
7. Negative and `INCONCLUSIVE_*` scientific outcomes count as completed execution; thresholds
   are never changed to obtain PASS.
8. After one bounded refreeze/rerun, remaining changes stop the workflow for inspection.

## Sub-hour configuration

`.ai-flow/config.complete-subhour.toml` uses one code-repair opportunity plus one reserved
verification review, two evidence generations and two new artifacts per configured series. All
Python implementation/tests, ai-flow
prompts/tasks/schemas, the active config and the monthly-search policy are protected sources.
Phase 3, Phase 4, WindowCostIndex, full-month and Gate S registration/outcome series have
independent append-only budgets.

The interrupted legacy run `20260831-003825-46483` remains preserved. It must not be resumed
under the staged policy because its initial registration baseline was not checkpointed by that
protocol. A new run is required after this controller change passes review.
