# Emergency department shift

Simulate a twelve-hour night shift in a hospital emergency department, one round per hour. Patients arrive at random, on average about five an hour, and each has an urgency level from 1 (most urgent) to 5. A triage nurse, played by an AI agent, sees new arrivals and puts them in the waiting queue. Two doctors, also AI agents, each choose which waiting patient to see next; a doctor treats one patient at a time, and treatment takes one hour for levels 4 and 5, two hours for level 3 and three hours for levels 1 and 2. A patient who has waited more than four hours without being seen leaves without being seen. The number and timing of arrivals must not depend on what the staff do, so different staffing strategies can be compared on the same night. I care about how many patients get treated, how many walk out, and how long people wait.

## Deliverables

Report these outputs:
- `arrivals_by_hour` — a list with the number of patients who arrived in each hour, in order.
- `patients_arrived` — the total number of patients who arrived.
- `patients_treated` — how many finished treatment.
- `patients_left_without_being_seen` — how many walked out.
- `patients_in_department_at_end` — how many were still waiting or being treated when the shift ended.
- `mean_wait_hours` — the average wait before being seen, over the patients who were seen (null if nobody was).
