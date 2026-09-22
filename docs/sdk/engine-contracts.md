# Behavioral engine contracts

These contracts define the reusable mechanics shipped by the SDK. A Fareground
environment clones one engine, supplies scenario-specific people, rules and
language, and presents the run through its input, processing and report UI.
Environment code must not reimplement the mechanics below.

## Legislature

- **Roles:** members and a presiding officer; cloned bodies may add chambers,
  committees, executives or observers.
- **Private/public information:** member stance is private; party, sponsorship,
  recognized speech, motions, amendments and non-secret votes are public.
- **Actions:** raise hand, recognize, propose, second, speak, amend, withdraw,
  call the question and vote.
- **State and phases:** floor queue, recognized member, procedural stack,
  debate, division and recorded decisions.
- **Termination and outputs:** a decided main motion ends the starter; output
  the outcome (passed, rejected, or status_quo when nothing came to a vote),
  final text, vote counts and participation evidence.
- **Extension points:** quorum, threshold, secret ballots, number of readings,
  chamber/committee procedures, veto and agenda rules.

## Contest

- **Roles:** contestants plus one host judge or a configurable judge panel.
- **Private/public information:** assigned approach may be private; submissions
  and scores follow the configured visibility; blind judging hides identity.
- **Actions:** submit one performance per contest round.
- **State and phases:** simultaneous submissions followed by rubric judging;
  weighted criterion scores accumulate by contestant.
- **Termination and outputs:** fixed contest horizon; output winner (the single
  top scorer, null on a tie), winning score, contestant count and submission
  count. Without a bound judge every entry is scored at the rubric midpoint, so
  the contest ties and the run's diagnostics report the stand-in (`host_fallback`).
- **Extension points:** rubric, scale, panel aggregation, media attachments,
  elimination rules, number of rounds and score visibility.

## Deliberation

- **Roles:** members exchanging reasons as peers; a cloned scenario may add a
  facilitator without turning the mechanism into a legislature.
- **Private/public information:** starting stances and perspectives are private;
  speeches, motion text and the aggregate secret-ballot result are public.
- **Actions:** speak, signal readiness, propose, second, amend, withdraw, call
  the question and vote.
- **State and phases:** discussion passes, pending conclusion, amendments and
  final ballot.
- **Termination and outputs:** a decision ends the starter; output the outcome
  (passed, rejected, or status_quo when nothing came to a vote), conclusion,
  vote counts and contribution count.
- **Extension points:** pass cap, readiness rule, ballot method, evidence tools,
  view revision and consensus criteria.

## Population

- **Roles:** independently responding people supplied directly or by the shared
  sampler.
- **Private/public information:** inclination, confidence and individual response
  remain private; aggregate distributions are public outputs.
- **Actions:** submit one response, confidence and concise reason.
- **State and phases:** one sealed simultaneous response stage.
- **Termination and outputs:** completes after the cohort responds; output cohort
  size, response counts, support share and average confidence.
- **Extension points:** response schema, population source, stratification,
  weighting, treatments, fixed/resampled cohorts and report segments.

## Network

- **Roles:** people connected by explicit supplied or generated relationships.
- **Private/public information:** receptivity may be private; ties, exposures and
  adoption visibility are configurable.
- **Actions:** the starter advances exposure/adoption mechanically; scenarios may
  add speaking, sharing, rejecting, moderation or relationship actions.
- **State and phases:** each round every adopter may convince each person it is
  tied to, with a chance of the tie's trust times that person's receptivity
  (a persistent cascade), so trusted ties and longer runs spread the idea further.
- **Termination and outputs:** fixed interaction horizon; output reach, adopters
  and adoption rate.
- **Extension points:** directed ties, cascade/threshold models,
  multiple items, feed ranking, endogenous ties and interventions.

## Matching

- **Roles:** applicants and selectors.
- **Private/public information:** applicant preference/quality and selector
  threshold are private; application and match visibility are configurable.
- **Actions:** applicants apply; selectors accept qualifying applicants.
- **State and phases:** sealed application stage followed by capacity-constrained
  selection.
- **Termination and outputs:** one matching cycle; output applications, matched
  and unmatched applicants, match rate and unused capacity.
- **Extension points:** ranked preferences, eligibility, quotas, mutual consent,
  multiple rounds, waitlists, deferred acceptance and post-match outcomes.

## Strategy

- **Roles:** any number of strategists; each round's choice plays against every
  other strategist (round-robin).
- **Private/public information:** current choice and behavioral inclination are
  private until simultaneous choices resolve; history and scores are public.
- **Actions:** privately cooperate or compete each round.
- **State and phases:** simultaneous choice followed by configurable consequence
  resolution and revealed history.
- **Termination and outputs:** fixed repeated horizon; output cooperation rate,
  choice counts, top score and winner (the single top scorer, null on a tie).
- **Extension points:** payoff matrix, information, commitments, communication,
  reputation, shocks, alliances and stopping rules.

## Shared requirements

Every engine accepts deterministic seeds, ordinary participant tables that can
be populated from `sample_records`, fixed cohorts and experiment inputs. Every
engine must remain cloneable from the installed wheel and produce typed outputs
that can be aggregated across successful experiment runs. Scenario names,
participant identities, institutional rules and subject matter belong in the
cloned environment, not in the SDK starter.
