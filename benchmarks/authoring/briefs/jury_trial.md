# Jury trial

I want to simulate a short criminal jury trial where every participant is an AI agent: a prosecutor, a defense attorney, a judge and six jurors. The prosecution has four exhibits it can offer (a CCTV still, a till receipt, a witness statement and a phone record) and the defense has two (an alibi letter and a bank statement). Each exhibit has a short description of what it shows. When one side offers an exhibit, the other side may object, and the judge then sustains or overrules the objection. An exhibit that nobody objects to, or whose objection is overruled, is admitted and the jurors get to read what it shows. A sustained objection keeps that exhibit out for good, and the jurors must never see its contents. Give the evidence phase a few rounds, then each lawyer makes a closing statement. Finally each juror votes guilty or not guilty. A guilty or a not-guilty verdict needs all six jurors to agree; anything else is a hung jury.

## Deliverables

Report these outputs:
- `verdict` — exactly "guilty", "not guilty" or "hung".
- `juror_votes` — a map from each juror's id to "guilty" or "not guilty" (null for a juror who never voted).
- `admitted_exhibits` — a list of the names of the exhibits that were admitted.
- `excluded_exhibits` — a list of the names of the exhibits kept out by a sustained objection.
- `objections_sustained` — how many objections the judge sustained.
