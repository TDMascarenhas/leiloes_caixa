-- Migração: schema single-user → multiusuário
-- Rode isto UMA VEZ no seu projeto Supabase JÁ EXISTENTE (o que já tem
-- filtros/imoveis com dados). Se está montando um projeto do ZERO, use
-- schema.sql em vez deste arquivo.
--
-- É seguro rodar tudo de uma vez, de cima a baixo, num projeto que já
-- tem o schema antigo. Faz sentido rodar como o USUÁRIO QUE JÁ USA O
-- SISTEMA HOJE (logado no painel, pra auth.uid() resolver certo nos
-- comandos abaixo) — mas os comandos de DDL (create table, alter table)
-- rodam igual pelo SQL Editor independente de quem está "logado" ali,
-- já os de dados (insert) abaixo dependem de você colar o seu user_id.

-- 1) filtros ganha dono — como já existem filtros seus no banco, primeiro
--    adiciona a coluna aceitando nulo, depois preenche com o SEU user_id,
--    e só então torna obrigatória.
alter table filtros add column if not exists user_id uuid references auth.users(id) on delete cascade;

-- Pegue seu user_id em Authentication → Users (copie o UUID da sua conta)
-- e cole no lugar de 'COLE-SEU-USER-ID-AQUI' nas duas linhas abaixo:
update filtros set user_id = 'COLE-SEU-USER-ID-AQUI' where user_id is null;

alter table filtros alter column user_id set not null;
alter table filtros alter column user_id set default auth.uid();
create index if not exists idx_filtros_user on filtros (user_id);

-- 2) tabela de junção filtro × imóvel (substitui a coluna filtros_correspondentes)
create table if not exists filtro_imovel (
  filtro_id uuid not null references filtros(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  criado_em timestamptz not null default now(),
  primary key (filtro_id, imovel_id)
);
create index if not exists idx_filtro_imovel_imovel on filtro_imovel (imovel_id);

-- popula a junção a partir do que já existe em imoveis.filtros_correspondentes
-- (casando pelo nome do filtro — funciona porque hoje só existe 1 usuário,
-- então nome de filtro não colide entre usuários diferentes ainda)
insert into filtro_imovel (filtro_id, imovel_id)
select f.id, im.id
from imoveis im
cross join lateral unnest(im.filtros_correspondentes) as nome_filtro
join filtros f on f.nome = nome_filtro
on conflict do nothing;

-- 3) tabela de favorito/ignorado por usuário
create table if not exists interesse_usuario (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  favorito boolean not null default false,
  ignorado boolean not null default false,
  atualizado_em timestamptz not null default now(),
  unique (user_id, imovel_id)
);

-- migra os favoritos/ignorados que já existem em imoveis pra cá, atribuindo
-- ao SEU user_id (troque de novo abaixo):
insert into interesse_usuario (user_id, imovel_id, favorito, ignorado)
select 'COLE-SEU-USER-ID-AQUI', id, favorito, ignorado
from imoveis
where favorito = true or ignorado = true
on conflict (user_id, imovel_id) do nothing;

-- 4) tira favorito/ignorado/filtros_correspondentes de imoveis — não são
--    mais usados dali, agora vêm de interesse_usuario e filtro_imovel
alter table imoveis drop column if exists favorito;
alter table imoveis drop column if exists ignorado;
alter table imoveis drop column if exists filtros_correspondentes;

-- 5) a view que o painel usa
create or replace view meus_imoveis
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
where f.id is not null
   or coalesce(iu.favorito, false) = true
   or coalesce(iu.ignorado, false) = true
group by im.id, iu.favorito, iu.ignorado;

-- 6) troca as policies antigas pelas novas, com dono
alter table filtro_imovel enable row level security;
alter table interesse_usuario enable row level security;

drop policy if exists "filtros_leitura" on filtros;
drop policy if exists "filtros_escrita" on filtros;
drop policy if exists "filtros_edicao" on filtros;
drop policy if exists "filtros_exclusao" on filtros;
create policy "filtros_leitura" on filtros for select using (user_id = auth.uid());
create policy "filtros_escrita" on filtros for insert with check (user_id = auth.uid());
create policy "filtros_edicao" on filtros for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "filtros_exclusao" on filtros for delete using (user_id = auth.uid());

drop policy if exists "imoveis_favoritar" on imoveis;
drop policy if exists "imoveis_exclusao" on imoveis;
-- (a policy "imoveis_leitura" continua igual — dado compartilhado)

create policy "filtro_imovel_leitura" on filtro_imovel
  for select using (
    exists (select 1 from filtros f where f.id = filtro_imovel.filtro_id and f.user_id = auth.uid())
  );

create policy "interesse_leitura" on interesse_usuario for select using (user_id = auth.uid());
create policy "interesse_escrita" on interesse_usuario for insert with check (user_id = auth.uid());
create policy "interesse_edicao" on interesse_usuario for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "interesse_exclusao" on interesse_usuario for delete using (user_id = auth.uid());

-- Pronto. Depois de rodar tudo isso, atualize o painel/index.html e o
-- scripts/buscar_imoveis.py pelas versões novas.
