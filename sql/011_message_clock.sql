-- Message times are the time of the insert, not the start of the surrounding transaction (a run's transaction can
-- start before its reply is written, which made replies sort before the question they answer).
alter table messages alter column created_at set default clock_timestamp();
