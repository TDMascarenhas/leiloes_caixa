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
-- PERFIL_USUARIO — cada usuário cadastra o próprio chat_id do Telegram
-- aqui (um bot só, criado uma vez no BotFather; o chat_id é o que muda
-- de pessoa pra pessoa). O robô só avisa quem tiver chat_id preenchido.
-- ---------------------------------------------------------------------
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
-- o robô (service_role) lê a tabela inteira pra saber quem notificar,
-- ignorando RLS normalmente — não precisa de policy extra pra isso

-- ---------------------------------------------------------------------
-- Criar usuários: continua manual, em Authentication → Users → Add user
-- (e-mail + senha), um por pessoa. Não existe tela de cadastro público —
-- cada pessoa só entra com um login que você (ou quem administra o
-- Supabase) criou pra ela.
-- ---------------------------------------------------------------------

-- =====================================================================
-- v3 (redesign e novas funções), também disponível em migracao_v3.sql
-- =====================================================================
-- Migração v3: redesign + novas funções
-- Rode UMA VEZ no SQL Editor do Supabase (projeto já existente). É seguro
-- rodar de novo: tudo usa "if not exists" / "drop ... if exists".

-- 1) imóvel: aceita financiamento (vem da coluna "Financiamento" do CSV)
alter table imoveis add column if not exists financiamento boolean;

-- 2) "Remover da lista": some até o imóvel mudar de preço ou de modalidade
alter table interesse_usuario add column if not exists removido boolean not null default false;
alter table interesse_usuario add column if not exists removido_preco numeric;
alter table interesse_usuario add column if not exists removido_modalidade text;

-- 3) preferências de notificação
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

-- =====================================================================
-- v4 (também disponível em migracao_v4.sql)
-- =====================================================================
-- Migração v4: filtros avançados no alerta, resumo único das 20h e código de
-- conexão de uso único no QR Code do Telegram.
-- Rode UMA VEZ no SQL Editor do Supabase. É seguro rodar de novo.
-- Rode ANTES de atualizar o painel, o robô e a função do Telegram.

-- 1) critérios avançados dentro do alerta (tipo, quartos, desconto, área,
--    modalidade, financiamento). Vazio = alerta sem critério extra, como hoje.
alter table filtros add column if not exists criterios jsonb;

-- 2) como a pessoa quer receber os avisos: um por imóvel ou um resumo único
alter table perfil_usuario add column if not exists formato_aviso text not null default 'individual'; -- individual | resumo

-- 3) código de uso único do QR Code (substitui o identificador da conta no link)
alter table perfil_usuario add column if not exists vinculo_token text;
alter table perfil_usuario add column if not exists vinculo_expira timestamptz;
create index if not exists idx_perfil_vinculo_token on perfil_usuario (vinculo_token);

-- 4) fila dos avisos que esperam o resumo das 20h (só o robô lê e grava)
create table if not exists aviso_pendente (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  imovel_id uuid not null references imoveis(id) on delete cascade,
  tipo text not null,              -- novo | mudou
  detalhe text,
  criado_em timestamptz not null default now()
);
create index if not exists idx_aviso_pendente_user on aviso_pendente (user_id);
alter table aviso_pendente enable row level security;   -- sem policies: só o robô (service_role)

-- 5) liberações (desde 2026 o Supabase não libera tabela nova sozinho)
grant select, insert, update, delete on public.aviso_pendente to service_role;

-- =====================================================================
-- v5 (também disponível em migracao_v5.sql)
-- =====================================================================
-- Migração v5: CEP do imóvel (lido da página de detalhe da Caixa) para o link do mapa.
-- Rode UMA VEZ no SQL Editor do Supabase. É seguro rodar de novo.
-- Rode ANTES de atualizar o robô e o painel (o robô passa a gravar a coluna "cep").

-- 1) a coluna nova
alter table imoveis add column if not exists cep text;

-- 2) a view precisa ser recriada: ela usa "im.*", e o Postgres fixa a lista de colunas
--    na hora em que a view é criada, então a coluna nova só aparece depois disto
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

-- 3) liberação (desde 2026 o Supabase não libera objeto novo sozinho)
grant select on public.meus_imoveis to authenticated;

-- =====================================================================
-- Liberações de acesso para o painel e o robô (também em liberar_acesso.sql)
-- =====================================================================
-- Libera as tabelas e a view para o painel (login) e para o robô (service_role).
-- Desde 2026 o Supabase NÃO libera objetos novos para a API automaticamente: sem estes
-- GRANTs o painel recebe "permission denied" ao ler. É seguro rodar mais de uma vez.
--
-- Isto NÃO enfraquece a segurança: quem vê o quê continua sendo decidido pelas regras de
-- RLS de cada tabela (cada pessoa só enxerga as próprias linhas) e o papel "anon" (sem
-- login) não recebe nada. Aqui se dá a cada papel no máximo o que as regras já permitem.

-- painel (pessoa logada)
grant select                         on public.imoveis           to authenticated;
grant select                         on public.historico         to authenticated;
grant select                         on public.status_execucao   to authenticated;
grant select, delete                 on public.filtro_imovel     to authenticated;
grant select, insert, update, delete on public.filtros           to authenticated;
grant select, insert, update, delete on public.interesse_usuario to authenticated;
grant select, insert, update, delete on public.custo_imovel      to authenticated;
grant select, insert, update         on public.perfil_usuario    to authenticated;
grant select                         on public.meus_imoveis      to authenticated;

-- robô e Edge Function (service_role): lê e grava em tudo
grant select, insert, update, delete on public.filtros           to service_role;
grant select, insert, update, delete on public.imoveis           to service_role;
grant select, insert, update, delete on public.filtro_imovel     to service_role;
grant select, insert, update, delete on public.interesse_usuario to service_role;
grant select, insert, update, delete on public.historico         to service_role;
grant select, insert, update, delete on public.perfil_usuario    to service_role;
grant select, insert, update, delete on public.custo_imovel      to service_role;
grant select, insert, update, delete on public.lembrete_enviado  to service_role;
grant select, insert, update, delete on public.status_execucao   to service_role;
grant select, insert, update, delete on public.aviso_pendente    to service_role;
