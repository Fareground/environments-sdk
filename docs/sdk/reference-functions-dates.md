# functions / dates

## Functions: dates

- `$date_add(date, n, unit?)` — The date `n` units after `date` (unit day, week, month, quarter, year, hour or minute; default day; a negative `n` goes back). Months keep the day, or take the month's last day: $date_add('2026-01-31', 1, month) is '2026-02-28'.
- `$date_part(date, part)` — A part of a date: year, quarter (1–4), month (1–12), day, weekday (1 Monday … 7 Sunday), week (ISO week of the year, 1–53), day_of_year, hour, minute, weekday_name ('Monday') or month_name ('September').
- `$days_between(a, b)` — Days from date `a` to date `b`: negative when `b` is earlier, fractional between date-times ($days_between('2026-09-01', '2026-09-15') is 14).
- `$is_holiday(date, dates)` — True when `date`'s day is one of `dates`: ISO date texts, or rows with a `date` field (a holidays table).
