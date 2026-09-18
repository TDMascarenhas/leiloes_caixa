-- Schema do "Radar de Leilões Caixa" — versão multiusuário
-- Rode isso no SQL Editor do seu projeto Supabase (projeto novo, do zero).
-- Se você já tem o schema antigo (single-user) rodando, use
-- migracao_multiusuario.sql em vez deste arquivo.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------
-- FILTROS — cada um pertence a um usuário (dono definido automaticamente
-- pelo login de quem cria, via default auth.uid() — o app nunca precisa
-- mandar isso explicitamente).
-- ---------------------------------------------------------------------
create table if not exists filtros (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  nome text not null,
  estado text not null,                  -- UF, ex: 'BA'
  cidade text not null,                   -- ex: 'Salvador'
  bairros text[] not null default '{}',  -- vazio = qualquer bairro
  valor_min numeric not null default 0,
  valor_max numeric,                      -- null = sem limite
  ativo boolean not null default true,
  criado_em timestamptz not null default now()
);

create index if not exists idx_filtros_user on filtros (user_id);

-- ---------------------------------------------------------------------
-- IMOVEIS — cache ÚNICO e COMPARTILHADO entre todos os usuários (é a
-- mesma informação pública da Caixa pra todo mundo; não faz sentido
-- duplicar). Não tem dono, não tem favorito/ignorado aqui — isso é
-- decisão pessoal de cada usuário, fica em `interesse_usuario`.
-- ---------------------------------------------------------------------
create table if not exists imoveis (
  id uuid primary key default gen_random_uuid(),
  uf text not null,
  numero_imovel text not null,           -- número bruto do CSV, usado como chave
  numero_formatado text,                  -- número como aparece no edital (ex: 878771431187-5)
  empreendimento text,                    -- nome do condomínio/empreendimento, quando informado no edital
  cidade text,
  bairro text,
  endereco text,
  valor_avaliacao numeric,
  preco numeric,                          -- valor da modalidade atual (CSV de hoje)
  desconto text,
  modalidade text,
  descricao text,                         -- texto bruto da coluna Descrição do CSV
  fatos jsonb,                             -- tipo/quartos/área/amenidades já extraídos da descrição
  link text,
  foto_url text,
  matricula text,
  inscricao_imobiliaria text,             -- também usado como "IPTU"
  comarca text,
  edital text,
  leiloeiro text,
  valor_1_leilao numeric,
  data_1_leilao text,
  valor_2_leilao numeric,
  data_2_leilao text,
  primeira_vez_visto timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  unique (uf, numero_imovel)
);

create index if not exists idx_imoveis_uf_cidade on imoveis (uf, cidade);
create index if not exists idx_imoveis_valor on imoveis (valor_avaliacao);

-- ---------------------------------------------------------------------
-- FILTRO_IMOVEL — junção: quais imóveis batem em qual filtro (de quem).
-- Quem escreve aqui é só o robô Python (service_role). A cada execução,
-- ele apaga as associações antigas de cada filtro processado e grava as
-- atuais — então um imóvel que deixou de bater (porque você editou o
-- filtro, por exemplo) simplesmente some daqui sozinho no próximo dia.
-- ---------------------------------------------------------------------
create table if not exists filtro_imovel (
  filtro_id uuid not null references filtros(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  criado_em timestamptz not null default now(),
  primary key (filtro_id, imovel_id)
);

create index if not exists idx_filtro_imovel_imovel on filtro_imovel (imovel_id);

-- ---------------------------------------------------------------------
-- INTERESSE_USUARIO — favorito/ignorado são pessoais: cada usuário tem
-- sua própria linha por imóvel. Fica assim mesmo que o filtro que achou
-- aquele imóvel seja editado ou excluído depois.
-- ---------------------------------------------------------------------
create table if not exists interesse_usuario (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  favorito boolean not null default false,
  ignorado boolean not null default false,
  atualizado_em timestamptz not null default now(),
  unique (user_id, imovel_id)
);

-- ---------------------------------------------------------------------
-- HISTORICO — mudanças de modalidade/preço de um imóvel ao longo do
-- tempo. É do imóvel em si (compartilhado), não de um usuário.
-- ---------------------------------------------------------------------
create table if not exists historico (
  id uuid primary key default gen_random_uuid(),
  uf text not null,
  numero_imovel text not null,
  modalidade text,
  preco numeric,
  detectado_em timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- VIEW meus_imoveis — o painel lê só daqui pra montar Resultados e
-- Descartados. Junta imóvel + meus filtros que bateram nele + meu
-- favorito/ignorado, já filtrado pro usuário logado (graças ao
-- security_invoker, o auth.uid() usado dentro da view é o de quem está
-- consultando, não o de quem criou a view).
--
-- Um imóvel aparece aqui se: bate em algum filtro MEU ativo agora, OU eu
-- já favoritei/ignorei ele antes (mesmo que o filtro que achou já tenha
-- sido excluído ou editado — favorito e ignorado são permanentes até
-- você mesmo desfazer).
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- RLS — cada usuário só vê/mexe no que é dele. `imoveis` continua
-- legível por qualquer autenticado (dado público da Caixa), mas o que
-- decide o que aparece pra CADA usuário é a view meus_imoveis acima,
-- que já filtra por dono. `filtro_imovel` só é legível através dos seus
-- próprios filtros — quem escreve ali é só o robô (service_role).
-- ---------------------------------------------------------------------

alter table filtros enable row level security;
alter table imoveis enable row level security;
alter table filtro_imovel enable row level security;
alter table interesse_usuario enable row level security;
alter table historico enable row level security;

create policy "filtros_leitura" on filtros
  for select using (user_id = auth.uid());
create policy "filtros_escrita" on filtros
  for insert with check (user_id = auth.uid());
create policy "filtros_edicao" on filtros
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "filtros_exclusao" on filtros
  for delete using (user_id = auth.uid());

create policy "imoveis_leitura" on imoveis
  for select using (auth.role() = 'authenticated');
-- nenhuma policy de insert/update/delete pra 'imoveis' via anon/autenticado:
-- só o robô Python (service_role, que ignora RLS) grava aqui

create policy "filtro_imovel_leitura" on filtro_imovel
  for select using (
    exists (select 1 from filtros f where f.id = filtro_imovel.filtro_id and f.user_id = auth.uid())
  );
-- nenhuma policy de insert/update/delete via anon/autenticado: só o robô grava

create policy "interesse_leitura" on interesse_usuario
  for select using (user_id = auth.uid());
create policy "interesse_escrita" on interesse_usuario
  for insert with check (user_id = auth.uid());
create policy "interesse_edicao" on interesse_usuario
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "interesse_exclusao" on interesse_usuario
  for delete using (user_id = auth.uid());

create policy "historico_leitura" on historico
  for select using (auth.role() = 'authenticated');
-- só o robô Python (service_role) grava no histórico

-- ---------------------------------------------------------------------
-- Criar usuários: continua manual, em Authentication → Users → Add user
-- (e-mail + senha), um por pessoa. Não existe tela de cadastro público —
-- cada pessoa só entra com um login que você (ou quem administra o
-- Supabase) criou pra ela.
-- ---------------------------------------------------------------------
