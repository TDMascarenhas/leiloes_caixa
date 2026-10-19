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
