# Classroom with weekly quizzes

Simulate a six-week course with one teacher and five students, all AI agents. The course covers three topics: fractions, decimals and percentages. Every week the teacher chooses one topic to teach, and each student chooses whether to study hard or take it easy that week. A student's understanding of a topic grows when it is taught and grows more when they study. At the end of every week there is a quiz on that week's topic, scored from 0 to 10, based on the student's understanding of the topic plus some luck. Students see only their own quiz scores; the teacher sees everyone's. A student's final grade is the average of their quiz scores.

## Deliverables

Report these outputs:
- `quiz_scores` — a map from each student's id to the list of their quiz scores, one per week in order.
- `final_grades` — a map from each student's id to their final grade.
- `class_average` — the average of the final grades.
