create or replace function public.create_profile_for_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
declare
  requested_role text;
begin
  requested_role := lower(trim(coalesce(new.raw_user_meta_data ->> 'role', 'parent')));

  if requested_role = 'parent' then
    requested_role := 'parent';
  elsif requested_role in ('asha', 'asha worker') then
    requested_role := 'asha';
  else
    raise exception 'Unsupported account role: %', requested_role;
  end if;

  insert into public.profiles (id, email, full_name, role)
  values (
    new.id,
    lower(new.email),
    coalesce(new.raw_user_meta_data ->> 'full_name', ''),
    requested_role
  )
  on conflict (id) do update
    set email = coalesce(public.profiles.email, excluded.email);

  return new;
end;
$$;
