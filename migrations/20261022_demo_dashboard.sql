begin;

alter table public.profiles
  add column if not exists email text,
  add column if not exists is_demo boolean not null default false;

update public.profiles as profile
set email = auth_user.email
from auth.users as auth_user
where profile.id = auth_user.id
  and profile.email is null;

alter table public.profiles
  alter column mobile_number drop not null;

create unique index if not exists profiles_email_unique_idx
  on public.profiles (lower(email))
  where email is not null;

create table if not exists public.villages (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  district text,
  taluka text,
  created_at timestamptz not null default now(),
  is_demo boolean not null default false
);
create unique index if not exists villages_name_taluka_district_unique_idx
  on public.villages (lower(name), lower(coalesce(taluka, '')), lower(coalesce(district, '')));

create table if not exists public.asha_workers (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null unique references public.profiles(id) on delete cascade,
  village_id uuid not null references public.villages(id) on delete restrict,
  worker_code text unique,
  created_at timestamptz not null default now(),
  is_demo boolean not null default false
);

create table if not exists public.children (
  id uuid primary key default gen_random_uuid(),
  guardian_id uuid references public.profiles(id) on delete cascade,
  asha_id uuid references public.asha_workers(id) on delete set null,
  village_id uuid references public.villages(id) on delete set null,
  name text not null,
  surname text not null,
  dob date not null,
  guardian_name text not null,
  guardian_phone text,
  gender text check (gender in ('Female', 'Male')),
  created_at timestamptz not null default now(),
  is_demo boolean not null default false
);

create table if not exists public.vaccine_schedule (
  id uuid primary key default gen_random_uuid(),
  vaccine text not null,
  due_age_days integer not null check (due_age_days >= 0),
  created_at timestamptz not null default now(),
  is_demo boolean not null default false
);
create unique index if not exists vaccine_schedule_vaccine_age_unique_idx
  on public.vaccine_schedule (vaccine, due_age_days);

create table if not exists public.vaccination_records (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  vaccine text not null,
  due_date date not null,
  given_date date,
  status text not null check (status in ('completed', 'pending', 'overdue')),
  created_at timestamptz not null default now(),
  is_demo boolean not null default false,
  check (
    (status = 'completed' and given_date is not null)
    or (status in ('pending', 'overdue') and given_date is null)
  )
);

create table if not exists public.risk_predictions (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  asha_id uuid references public.asha_workers(id) on delete set null,
  dropout_probability double precision not null check (dropout_probability between 0 and 1),
  risk_level text not null check (risk_level in ('Normal', 'Low', 'Medium', 'High')),
  priority_score double precision not null,
  reasons jsonb not null default '[]'::jsonb,
  scored_at timestamptz not null default now(),
  is_demo boolean not null default false
);

create table if not exists public.interventions (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  asha_id uuid not null references public.asha_workers(id) on delete cascade,
  type text not null check (type in ('call', 'reminder', 'home_visit', 'followup')),
  note text not null default '',
  status text not null default 'scheduled',
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  is_demo boolean not null default false
);

create table if not exists public.reminder_plans (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  plan jsonb not null,
  generated_at timestamptz not null default now(),
  is_demo boolean not null default false
);

create index if not exists asha_workers_village_id_idx on public.asha_workers (village_id);
create index if not exists children_guardian_id_idx on public.children (guardian_id);
create index if not exists children_asha_id_idx on public.children (asha_id);
create index if not exists children_village_id_idx on public.children (village_id);
create index if not exists vaccination_records_child_id_idx on public.vaccination_records (child_id);
create index if not exists vaccination_records_due_date_idx on public.vaccination_records (due_date);
create index if not exists vaccination_records_given_date_idx on public.vaccination_records (given_date);
create index if not exists risk_predictions_child_scored_idx on public.risk_predictions (child_id, scored_at desc);
create index if not exists risk_predictions_asha_risk_idx on public.risk_predictions (asha_id, risk_level);
create index if not exists interventions_child_id_idx on public.interventions (child_id);
create index if not exists interventions_asha_created_idx on public.interventions (asha_id, created_at desc);
create index if not exists reminder_plans_child_generated_idx on public.reminder_plans (child_id, generated_at desc);

create or replace function public.profile_has_role(expected_role text)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.profiles p
    where p.id = auth.uid()
      and (
        lower(trim(p.role)) = expected_role
        or (expected_role = 'asha' and lower(trim(p.role)) = 'asha worker')
      )
  );
$$;

create or replace function public.my_profile_role()
returns text
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select lower(trim(p.role))
  from public.profiles p
  where p.id = auth.uid()
  limit 1;
$$;

create or replace function public.is_my_asha(target_asha_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.asha_workers aw
    where aw.id = target_asha_id
      and aw.profile_id = auth.uid()
  );
$$;

create or replace function public.can_access_child(target_child_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.children c
    where c.id = target_child_id
      and (
        c.guardian_id = auth.uid()
        or public.is_my_asha(c.asha_id)
      )
  );
$$;

create or replace function public.can_access_village(target_village_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.asha_workers aw
    where aw.village_id = target_village_id
      and aw.profile_id = auth.uid()
  ) or exists (
    select 1
    from public.children c
    where c.village_id = target_village_id
      and c.guardian_id = auth.uid()
  );
$$;

revoke all on function public.profile_has_role(text) from public;
revoke all on function public.my_profile_role() from public;
revoke all on function public.is_my_asha(uuid) from public;
revoke all on function public.can_access_child(uuid) from public;
revoke all on function public.can_access_village(uuid) from public;
grant execute on function public.profile_has_role(text) to authenticated;
grant execute on function public.my_profile_role() to authenticated;
grant execute on function public.is_my_asha(uuid) to authenticated;
grant execute on function public.can_access_child(uuid) to authenticated;
grant execute on function public.can_access_village(uuid) to authenticated;

create or replace function public.create_profile_for_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
begin
  insert into public.profiles (id, email, full_name, role)
  values (
    new.id,
    lower(new.email),
    coalesce(new.raw_user_meta_data ->> 'full_name', ''),
    'parent'
  )
  on conflict (id) do update
    set email = coalesce(public.profiles.email, excluded.email);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_profile on auth.users;
create trigger on_auth_user_created_profile
  after insert on auth.users
  for each row execute function public.create_profile_for_auth_user();

do $$
declare
  table_name text;
  table_names text[] := array[
    'profiles',
    'villages',
    'asha_workers',
    'children',
    'vaccine_schedule',
    'vaccination_records',
    'risk_predictions',
    'interventions',
    'reminder_plans'
  ];
begin
  foreach table_name in array table_names loop
    execute format('alter table public.%I enable row level security', table_name);
  end loop;
end
$$;

do $$
declare
  policy_row record;
  table_names text[] := array[
    'profiles',
    'villages',
    'asha_workers',
    'children',
    'vaccine_schedule',
    'vaccination_records',
    'risk_predictions',
    'interventions',
    'reminder_plans'
  ];
begin
  for policy_row in
    select schemaname, tablename, policyname
    from pg_policies
    where schemaname = 'public'
      and tablename = any(table_names)
  loop
    execute format(
      'drop policy if exists %I on %I.%I',
      policy_row.policyname,
      policy_row.schemaname,
      policy_row.tablename
    );
  end loop;
end
$$;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated using (id = auth.uid());
drop policy if exists profiles_insert_own on public.profiles;
create policy profiles_insert_own on public.profiles
  for insert to authenticated with check (id = auth.uid() and role = 'parent' and is_demo = false);
drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update to authenticated using (id = auth.uid())
  with check (id = auth.uid() and lower(trim(role)) = public.my_profile_role());

drop policy if exists villages_select_assigned on public.villages;
create policy villages_select_assigned on public.villages
  for select to authenticated using (public.can_access_village(id));

drop policy if exists asha_workers_select_related on public.asha_workers;
create policy asha_workers_select_related on public.asha_workers
  for select to authenticated using (
    profile_id = auth.uid()
    or exists (
      select 1 from public.children c
      where c.asha_id = asha_workers.id and c.guardian_id = auth.uid()
    )
  );
drop policy if exists asha_workers_insert_self on public.asha_workers;
create policy asha_workers_insert_self on public.asha_workers
  for insert to authenticated with check (
    profile_id = auth.uid()
    and public.profile_has_role('asha')
    and is_demo = false
  );

drop policy if exists children_select_owned_or_assigned on public.children;
create policy children_select_owned_or_assigned on public.children
  for select to authenticated using (
    guardian_id = auth.uid()
    or (public.profile_has_role('asha') and public.is_my_asha(asha_id))
  );
drop policy if exists children_insert_owned_or_assigned on public.children;
create policy children_insert_owned_or_assigned on public.children
  for insert to authenticated with check (
    (
      guardian_id = auth.uid()
      or (public.profile_has_role('asha') and public.is_my_asha(asha_id))
    )
    and is_demo = false
  );

drop policy if exists children_update_owned_or_assigned on public.children;

drop policy if exists vaccine_schedule_select_authenticated on public.vaccine_schedule;
create policy vaccine_schedule_select_authenticated on public.vaccine_schedule
  for select to authenticated using (true);

drop policy if exists vaccination_records_select_related on public.vaccination_records;
create policy vaccination_records_select_related on public.vaccination_records
  for select to authenticated using (public.can_access_child(child_id));
drop policy if exists vaccination_records_insert_asha on public.vaccination_records;
create policy vaccination_records_insert_asha on public.vaccination_records
  for insert to authenticated with check (
    public.profile_has_role('asha')
    and exists (select 1 from public.children c where c.id = child_id and public.is_my_asha(c.asha_id))
    and is_demo = false
  );
drop policy if exists vaccination_records_update_asha on public.vaccination_records;
create policy vaccination_records_update_asha on public.vaccination_records
  for update to authenticated using (
    public.profile_has_role('asha')
    and exists (select 1 from public.children c where c.id = child_id and public.is_my_asha(c.asha_id))
  ) with check (
    public.profile_has_role('asha')
    and exists (select 1 from public.children c where c.id = child_id and public.is_my_asha(c.asha_id))
  );

drop policy if exists risk_predictions_select_related on public.risk_predictions;
create policy risk_predictions_select_related on public.risk_predictions
  for select to authenticated using (public.can_access_child(child_id));

drop policy if exists interventions_select_related on public.interventions;
create policy interventions_select_related on public.interventions
  for select to authenticated using (public.can_access_child(child_id));
drop policy if exists interventions_insert_assigned_asha on public.interventions;
create policy interventions_insert_assigned_asha on public.interventions
  for insert to authenticated with check (
    public.profile_has_role('asha')
    and public.is_my_asha(asha_id)
    and exists (select 1 from public.children c where c.id = child_id and c.asha_id = asha_id)
    and is_demo = false
  );
drop policy if exists interventions_update_assigned_asha on public.interventions;
create policy interventions_update_assigned_asha on public.interventions
  for update to authenticated using (
    public.profile_has_role('asha')
    and public.is_my_asha(asha_id)
    and exists (select 1 from public.children c where c.id = child_id and c.asha_id = asha_id)
  ) with check (
    public.profile_has_role('asha')
    and public.is_my_asha(asha_id)
    and exists (select 1 from public.children c where c.id = child_id and c.asha_id = asha_id)
  );

drop policy if exists reminder_plans_select_related on public.reminder_plans;
create policy reminder_plans_select_related on public.reminder_plans
  for select to authenticated using (public.can_access_child(child_id));

revoke all on public.profiles from public, anon, authenticated;
grant select on public.profiles to authenticated;
grant update (full_name, mobile_number) on public.profiles to authenticated;
revoke all on public.villages, public.asha_workers, public.children, public.vaccine_schedule,
  public.vaccination_records, public.risk_predictions, public.interventions, public.reminder_plans
  from public, anon, authenticated;
grant select on public.villages, public.vaccine_schedule to authenticated;
grant select on public.asha_workers to authenticated;
grant insert (profile_id, village_id, worker_code) on public.asha_workers to authenticated;
grant select on public.children to authenticated;
grant insert (
  guardian_id, asha_id, village_id, name, surname, dob, guardian_name, guardian_phone, gender
) on public.children to authenticated;
grant select, insert (child_id, vaccine, due_date, given_date, status)
  on public.vaccination_records to authenticated;
grant update (given_date, status) on public.vaccination_records to authenticated;
grant select on public.risk_predictions to authenticated;
grant select, insert (child_id, asha_id, type, note, status) on public.interventions to authenticated;
grant update (status, resolved_at) on public.interventions to authenticated;
grant select on public.reminder_plans to authenticated;

commit;
