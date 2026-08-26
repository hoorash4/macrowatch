-- AI output remains direction-validated by the policy pipeline. Administrators may
-- deliberately override the canonical reason and score without a direction lock.
alter table public.central_bank_policy_events
  drop constraint if exists central_bank_policy_events_normalization_direction_check;

alter table public.central_bank_policy_events
  add constraint central_bank_policy_events_ai_normalization_direction_check check (
    (ai_primary_reason <> 'normalization_hike' or action = 'hike')
    and (ai_primary_reason <> 'normalization_cut' or action = 'cut')
  );
