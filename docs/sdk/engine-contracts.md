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
  debate, division and recorded decisions. Each member's stance blends their
  starting stance with the average stance of the floor speeches so far, a
  personal share (averaging `persuasion`) coming from the speeches; a speech from
  another party counts `1 - party_loyalty` of one from the member's own. Members
  stay anchored to where they started, so the order of the members table does
  not decide the vote and debate does not collapse the chamber into unanimity.
  Coded members vote their current stance and never amend (amending is there for
  participants). The mover is recognized first; after that the coded chair
  recognizes a random raised hand, every coded member asks to speak once on the
  question, and the chair calls the question once no hand is left (at most 100
  passes, then the vote is forced).
- **Termination and outputs:** a decided main motion ends the starter; output
  the outcome (passed, rejected, or status_quo when nothing came to a vote),
  final text, vote counts and participation evidence.
- **Extension points:** quorum, threshold, secret ballots, number of readings,
  chamber/committee procedures, veto and agenda rules.

## Contest

- **Roles:** contestants plus one host judge or a configurable judge panel.
- **Private/public information:** assigned approach may be private; submissions
  and scores follow the configured visibility; blind judging hides identity.
- **Actions:** submit one performance per contest round. A contest needs at
  least two contestants.
- **State and phases:** simultaneous submissions followed by rubric judging;
  weighted criterion scores accumulate by contestant. Each submission also adds
  a hidden performance draw around the contestant's private `skill` (spread
  `luck`).
- **Termination and outputs:** fixed contest horizon; output winner (the single
  top scorer; a rubric tie goes to the stronger total performance, null when
  that ties too), whether a bound judge scored it (`judged`), winning score,
  contestant count and submission count. Without a bound judge every entry is
  scored at the rubric midpoint, so skill and luck decide: `judged` is false,
  `winning_score` is null and the run's diagnostics report the stand-in
  (`host_fallback`).
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
  final ballot. Each member's stance blends their starting stance with the
  average stance of the speeches so far, a personal share (averaging
  `persuasion`) coming from the speeches, so views move without collapsing into
  unanimity; members speak in a random order each pass. Coded members with
  a strong view either way put the question, anyone seconds it, and each speaks
  once per motion and votes their current stance, so an opposed group rejects the
  question rather than leaving it undecided. Coded members never amend.
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
- **State and phases:** one sealed simultaneous response stage. The coded
  baseline answers from each person's leaning: the inclination plus normal
  noise whose spread is one minus their confidence (support above 0.25, oppose
  below -0.25, otherwise undecided) and reports as its confidence the chance
  that leaning would land on the same answer again, so certain people with a
  clear inclination report high confidence and uncertain or borderline ones less.
- **Termination and outputs:** completes after the cohort responds; output cohort
  size, response counts, support share and average reported confidence.
- **Extension points:** response schema, population source, stratification,
  weighting, treatments, fixed/resampled cohorts and report segments.

## Network

- **Roles:** people connected by explicit supplied or generated relationships;
  `inputs.seeds` names the initial adopters (each must be in the participants
  table; `[]` for none), and a tie's trust must lie between 0 and 1.
- **Private/public information:** receptivity may be private; ties, exposures and
  adoption visibility are configurable.
- **Actions:** take up an idea one has heard about, turn it down (then one never
  takes it up or passes it on; adopters, the seed included, stay committed, so the
  idea cannot die at its source), or recommend it to one contact (who takes it up with a chance
  of the tie's trust times their receptivity). The coded baseline takes none of
  these and leaves the spread to word of mouth; scenarios may add speaking,
  moderation or relationship actions.
- **State and phases:** each round every adopter may convince each person it is
  tied to, with a chance of the tie's trust times that person's receptivity
  (a persistent cascade), so trusted ties and longer runs spread the idea further.
- **Termination and outputs:** fixed interaction horizon; output reach, adopters
  and adoption rate.
- **Extension points:** directed ties, cascade/threshold models,
  multiple items, feed ranking, endogenous ties and interventions.

## Matching

- **Roles:** applicants and selectors.
- **Private/public information:** rankings and selector thresholds are private;
  applicant quality and selector appeal and capacity are public.
- **Actions:** both sides privately rank the other, best first (unranked =
  unacceptable).
- **State and phases:** one sealed ranking stage, then deferred acceptance
  (`groups.matching`, applicants proposing) makes a stable match within each
  selector's capacity. Coded applicants rank selectors by appeal plus personal
  taste; coded selectors rank the applicants who meet their bar by quality plus
  taste (both with normal noise of spread `taste`).
- **Termination and outputs:** one matching cycle; output matched
  (`placement_matched`) and unmatched applicants, match rate, first-choice rate
  and unused capacity.
- **Extension points:** quotas, multiple rounds, waitlists, selector-proposing
  and post-match outcomes.

## Strategy

- **Roles:** any number of strategists; each round's choice plays against every
  other strategist (round-robin).
- **Private/public information:** current choice and strategy are private
  until simultaneous choices resolve; history and scores are public.
- **Coded strategies:** tit for tat (cooperate first, then compete only after
  most others competed last round), grim trigger (cooperate until anyone else
  ever competes), always cooperate, always compete and random ignore the payoffs
  by design; forward looking reads them: it cooperates while most others
  cooperated last round and `(mutual_cooperate - mutual_compete) × rounds left ≥
  compete_bonus - mutual_cooperate` (the cooperation still to come outweighs the
  one-round gain from competing), so a bigger temptation or a nearer end brings
  competition. Each coded move is replaced by a random one with probability
  `mistakes`. The payoffs are not required to form a prisoner's dilemma: any
  cooperate/compete matrix (a stag hunt, chicken) is accepted.
- **Actions:** privately cooperate or compete each round.
- **State and phases:** simultaneous choice followed by configurable consequence
  resolution and revealed history.
- **Termination and outputs:** fixed repeated horizon; output cooperation rate,
  choice counts, top score and winner (the single top scorer, null on a tie).
  A game needs at least two strategists: one alone is refused.
- **Extension points:** payoff matrix, information, commitments, communication,
  reputation, shocks, alliances and stopping rules.

## Shared requirements

A rate over nobody (a share of an empty population, a match rate without
applicants) is null, not 0. Every engine accepts deterministic seeds, ordinary participant tables that can
be populated from `sample_records`, fixed cohorts and experiment inputs. Every
engine must remain cloneable from the installed wheel and produce typed outputs
that can be aggregated across successful experiment runs. Scenario names,
participant identities, institutional rules and subject matter belong in the
cloned environment, not in the SDK starter.
