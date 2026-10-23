begin;

create table public.children (
  id uuid primary key default gen_random_uuid(),
  parent_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  date_of_birth date not null,
  gender text check (gender in ('Female', 'Male')),
  created_at timestamptz not null default now()
);

create index children_parent_id_idx on public.children (parent_id);

create table public.vaccines (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  recommended_age_days integer not null check (recommended_age_days >= 0),
  description text
);

create table public.immunization_records (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  vaccine_id uuid not null references public.vaccines(id) on delete restrict,
  status text not null default 'pending'
    check (status in ('pending', 'completed')),
  administered_by uuid references public.profiles(id) on delete set null,
  administered_date date,
  constraint immunization_completion_fields_check check (
    (status = 'pending' and administered_by is null and administered_date is null)
    or (status = 'completed' and administered_date is not null)
  ),
  unique (child_id, vaccine_id)
);

create index immunization_records_child_id_idx
  on public.immunization_records (child_id);
create index immunization_records_vaccine_id_idx
  on public.immunization_records (vaccine_id);

create or replace function public.has_current_profile_role(expected_role text)
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
        lower(trim(p.role)) = lower(trim(expected_role))
        or (
          lower(trim(expected_role)) = 'asha'
          and lower(trim(p.role)) = 'asha worker'
        )
      )
  );
$$;

revoke all on function public.has_current_profile_role(text) from public;
grant execute on function public.has_current_profile_role(text) to authenticated;

alter table public.children enable row level security;
alter table public.vaccines enable row level security;
alter table public.immunization_records enable row level security;

create policy children_select_parent_or_asha on public.children
  for select to authenticated
  using (
    (
      public.has_current_profile_role('parent')
      and parent_id = (select auth.uid())
    )
    or public.has_current_profile_role('asha')
  );

create policy children_insert_parent_own on public.children
  for insert to authenticated
  with check (
    public.has_current_profile_role('parent')
    and parent_id = (select auth.uid())
  );

create policy vaccines_select_authenticated on public.vaccines
  for select to authenticated
  using (true);

create policy immunization_records_select_parent_or_asha on public.immunization_records
  for select to authenticated
  using (
    public.has_current_profile_role('asha')
    or (
      public.has_current_profile_role('parent')
      and exists (
        select 1
        from public.children c
        where c.id = child_id
          and c.parent_id = (select auth.uid())
      )
    )
  );

create policy immunization_records_insert_asha on public.immunization_records
  for insert to authenticated
  with check (
    public.has_current_profile_role('asha')
    and administered_by = (select auth.uid())
  );

create policy immunization_records_update_asha on public.immunization_records
  for update to authenticated
  using (public.has_current_profile_role('asha'))
  with check (
    public.has_current_profile_role('asha')
    and administered_by = (select auth.uid())
  );

revoke all on public.children, public.vaccines, public.immunization_records
  from public, anon, authenticated;

grant select, insert (parent_id, name, date_of_birth, gender)
  on public.children to authenticated;
grant select on public.vaccines to authenticated;
grant select on public.immunization_records to authenticated;
grant insert (child_id, vaccine_id, status, administered_by, administered_date)
  on public.immunization_records to authenticated;
grant update (status, administered_by, administered_date)
  on public.immunization_records to authenticated;

commit;
