-- Rode isso no SQL Editor do seu projeto Supabase (já existente) — só
-- adiciona a tabela nova de perfil/Telegram, não mexe em mais nada.

create table if not exists perfil_usuario (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  telegram_chat_id text,
  atualizado_em timestamptz not null default now()
);

alter table perfil_usuario enable row level security;

create policy "perfil_leitura" on perfil_usuario
  for select using (user_id = auth.uid());
create policy "perfil_escrita" on perfil_usuario
  for insert with check (user_id = auth.uid());
create policy "perfil_edicao" on perfil_usuario
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
