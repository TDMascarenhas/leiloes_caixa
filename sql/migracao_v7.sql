-- Migração v7: prazo da licitação aberta e da venda direta/online, informado por quem usa o painel.
-- O prazo é um fato do imóvel, então é COMPARTILHADO: qualquer pessoa logada vê e pode informar/corrigir.
-- Rode UMA VEZ no SQL Editor do Supabase. É seguro rodar de novo.
-- Rode ANTES de atualizar o painel e o robô.

-- 1) o prazo de cada imóvel: data/hora da licitação, ou prazo final da venda direta/online
create table if not exists prazo_imovel (
  imovel_id uuid primary key references imoveis(id) on delete cascade,
  data_hora timestamptz not null,
  atualizado_em timestamptz not null default now(),
  atualizado_por uuid default auth.uid()
);
alter table prazo_imovel enable row level security;
drop policy if exists "prazo_leitura" on prazo_imovel;
drop policy if exists "prazo_insercao" on prazo_imovel;
drop policy if exists "prazo_edicao" on prazo_imovel;
drop policy if exists "prazo_exclusao" on prazo_imovel;
create policy "prazo_leitura"  on prazo_imovel for select to authenticated using (true);
create policy "prazo_insercao" on prazo_imovel for insert to authenticated with check (true);
create policy "prazo_edicao"   on prazo_imovel for update to authenticated using (true) with check (true);
create policy "prazo_exclusao" on prazo_imovel for delete to authenticated using (true);

-- 2) controle dos avisos de prazo já enviados (só o robô usa). A chave inclui o horário do prazo:
--    se o prazo for corrigido, os avisos valem de novo para o novo horário.
create table if not exists aviso_prazo_enviado (
  user_id uuid not null references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  chave text not null,
  enviado_em timestamptz not null default now(),
  primary key (user_id, imovel_id, chave)
);
alter table aviso_prazo_enviado enable row level security;   -- sem policies: só o robô (service_role)

-- 3) quais avisos de prazo cada pessoa quer (vazio = todos ligados)
alter table perfil_usuario add column if not exists avisos_prazo jsonb;

-- 4) liberações (desde 2026 o Supabase não libera objeto novo sozinho)
grant select, insert, update, delete on public.prazo_imovel        to authenticated;
grant select, insert, update, delete on public.prazo_imovel        to service_role;
grant select, insert, update, delete on public.aviso_prazo_enviado to service_role;
