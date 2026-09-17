-- Schema do "Radar de Leilões Caixa"
-- Rode isso no SQL Editor do seu projeto Supabase.

create extension if not exists "pgcrypto";

-- Filtros/alertas cadastrados por você no painel.
create table if not exists filtros (
  id uuid primary key default gen_random_uuid(),
  nome text not null,
  estado text not null,                  -- UF, ex: 'BA'
  cidade text not null,                   -- ex: 'Salvador'
  bairros text[] not null default '{}',  -- vazio = qualquer bairro
  valor_min numeric not null default 0,
  valor_max numeric,                      -- null = sem limite
  ativo boolean not null default true,
  criado_em timestamptz not null default now()
);

-- Imóveis encontrados que bateram em pelo menos um filtro — estado ATUAL.
create table if not exists imoveis (
  id uuid primary key default gen_random_uuid(),
  uf text not null,
  numero_imovel text not null,           -- número bruto do CSV, usado como chave
  numero_formatado text,                  -- número como aparece no edital (ex: 878771431187-5)
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
  filtros_correspondentes text[] not null default '{}',
  favorito boolean not null default false,
  primeira_vez_visto timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  unique (uf, numero_imovel)
);

create index if not exists idx_imoveis_uf_cidade on imoveis (uf, cidade);
create index if not exists idx_imoveis_valor on imoveis (valor_avaliacao);

-- Histórico: cada vez que um imóvel muda de modalidade/preço (ex: passou do 1º
-- pro 2º leilão), grava uma linha aqui em vez de simplesmente sobrescrever.
create table if not exists historico (
  id uuid primary key default gen_random_uuid(),
  uf text not null,
  numero_imovel text not null,
  modalidade text,
  preco numeric,
  detectado_em timestamptz not null default now()
);

-- RLS: o painel usa a chave "anon" no navegador, mas só libera qualquer
-- operação pra quem estiver autenticado (login feito no próprio painel via
-- Supabase Auth). Sem login, a chave anon sozinha não lê nem escreve nada.
-- Crie seu usuário em Authentication → Users → Add user, no painel do
-- Supabase (e-mail + senha) — não existe tela de cadastro público.

alter table filtros enable row level security;
alter table imoveis enable row level security;
alter table historico enable row level security;

create policy "filtros_leitura" on filtros
  for select using (auth.role() = 'authenticated');
create policy "filtros_escrita" on filtros
  for insert with check (auth.role() = 'authenticated');
create policy "filtros_edicao" on filtros
  for update using (auth.role() = 'authenticated');
create policy "filtros_exclusao" on filtros
  for delete using (auth.role() = 'authenticated');

create policy "imoveis_leitura" on imoveis
  for select using (auth.role() = 'authenticated');
-- autenticado pode fazer update (usado só pra marcar/desmarcar favorito no painel)
create policy "imoveis_favoritar" on imoveis
  for update using (auth.role() = 'authenticated')
  with check (auth.role() = 'authenticated');
-- nenhuma policy de insert/delete pra 'imoveis' via anon/autenticado:
-- só o script Python (chave service_role, que ignora RLS) grava aqui

create policy "historico_leitura" on historico
  for select using (auth.role() = 'authenticated');
-- só o script Python (service_role) grava no histórico
