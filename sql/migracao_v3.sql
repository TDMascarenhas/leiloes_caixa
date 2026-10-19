-- Migração v3: redesign + novas funções
-- Rode UMA VEZ no SQL Editor do Supabase (projeto já existente). É seguro
-- rodar de novo: tudo usa "if not exists" / "drop ... if exists".

-- 1) imóvel: aceita financiamento (vem da coluna "Financiamento" do CSV)
alter table imoveis add column if not exists financiamento boolean;

-- 2) "Remover da lista": some até o imóvel mudar de preço ou de modalidade
alter table interesse_usuario add column if not exists removido boolean not null default false;
alter table interesse_usuario add column if not exists removido_preco numeric;
alter table interesse_usuario add column if not exists removido_modalidade text;

-- 3) perfil do usuário (Telegram e preferências). Se a tabela ainda não existe
--    no seu banco, é criada aqui; se já existe, nada muda.
create table if not exists perfil_usuario (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  telegram_chat_id text,
  atualizado_em timestamptz not null default now()
);
alter table perfil_usuario enable row level security;
drop policy if exists "perfil_leitura" on perfil_usuario;
drop policy if exists "perfil_escrita" on perfil_usuario;
drop policy if exists "perfil_edicao" on perfil_usuario;
create policy "perfil_leitura" on perfil_usuario for select using (user_id = auth.uid());
create policy "perfil_escrita" on perfil_usuario for insert with check (user_id = auth.uid());
create policy "perfil_edicao" on perfil_usuario for update using (user_id = auth.uid()) with check (user_id = auth.uid());

-- preferências de notificação
alter table perfil_usuario add column if not exists avisar_novo boolean not null default true;
alter table perfil_usuario add column if not exists avisar_mudanca boolean not null default true;
alter table perfil_usuario add column if not exists lembrete_vespera text not null default 'favoritos'; -- favoritos | todos | nao

-- 4) cálculo de custo real salvo por usuário e imóvel
create table if not exists custo_imovel (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  dados jsonb not null,
  custo_total numeric,
  atualizado_em timestamptz not null default now(),
  primary key (user_id, imovel_id)
);
alter table custo_imovel enable row level security;
drop policy if exists "custo_leitura" on custo_imovel;
drop policy if exists "custo_escrita" on custo_imovel;
drop policy if exists "custo_edicao" on custo_imovel;
drop policy if exists "custo_exclusao" on custo_imovel;
create policy "custo_leitura" on custo_imovel for select using (user_id = auth.uid());
create policy "custo_escrita" on custo_imovel for insert with check (user_id = auth.uid());
create policy "custo_edicao" on custo_imovel for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "custo_exclusao" on custo_imovel for delete using (user_id = auth.uid());

-- 5) controle dos lembretes de véspera (evita mandar o mesmo lembrete duas vezes)
create table if not exists lembrete_enviado (
  user_id uuid not null references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  data_leilao text not null,
  enviado_em timestamptz not null default now(),
  primary key (user_id, imovel_id, data_leilao)
);
alter table lembrete_enviado enable row level security;
-- sem policies: só o robô (service_role) lê e grava

-- 6) situação de cada execução do robô (avisos de problema + faixa no painel)
create table if not exists status_execucao (
  id uuid primary key default gen_random_uuid(),
  executado_em timestamptz not null default now(),
  status text not null,          -- ok | parcial | falha
  assinatura text,               -- resumo do problema, usado pra não repetir aviso
  detalhes text
);
alter table status_execucao enable row level security;
drop policy if exists "status_leitura" on status_execucao;
create policy "status_leitura" on status_execucao for select using (auth.role() = 'authenticated');

-- 7) permitir que o dono exclua os resultados de um alerta seu (pausar e excluir)
drop policy if exists "filtro_imovel_exclusao" on filtro_imovel;
create policy "filtro_imovel_exclusao" on filtro_imovel
  for delete using (exists (select 1 from filtros f where f.id = filtro_imovel.filtro_id and f.user_id = auth.uid()));

-- 8) view recriada: esconde "removidos da lista" enquanto preço e modalidade
--    continuarem iguais ao momento da remoção (favorito nunca é escondido)
drop view if exists meus_imoveis;
create view meus_imoveis
with (security_invoker = true) as
select
  im.*,
  coalesce(iu.favorito, false) as favorito,
  coalesce(iu.ignorado, false) as ignorado,
  coalesce(array_agg(distinct f.nome) filter (where f.nome is not null), '{}') as filtros_correspondentes
from imoveis im
left join filtro_imovel fi on fi.imovel_id = im.id
left join filtros f on f.id = fi.filtro_id and f.user_id = auth.uid()
left join interesse_usuario iu on iu.imovel_id = im.id and iu.user_id = auth.uid()
where (f.id is not null or coalesce(iu.favorito, false) or coalesce(iu.ignorado, false))
  and not (
    coalesce(iu.removido, false)
    and not coalesce(iu.favorito, false)
    and iu.removido_preco is not distinct from im.preco
    and iu.removido_modalidade is not distinct from im.modalidade
  )
group by im.id, iu.favorito, iu.ignorado, iu.removido, iu.removido_preco, iu.removido_modalidade;

-- 9) liberar as tabelas novas para o painel (ver também liberar_acesso.sql)
grant select, insert, update, delete on public.perfil_usuario to authenticated;
grant select, insert, update, delete on public.custo_imovel   to authenticated;
grant select                         on public.status_execucao to authenticated;
grant select, insert, update, delete on public.perfil_usuario    to service_role;
grant select, insert, update, delete on public.custo_imovel      to service_role;
grant select, insert, update, delete on public.status_execucao   to service_role;
grant select, insert, update, delete on public.lembrete_enviado  to service_role;
