-- Migração v6: nome de exibição de cada pessoa ("Olá, ..." no topo do painel).
-- Substitui a lista de e-mails e nomes que ficava escrita no código do painel (que é público).
-- Rode UMA VEZ no SQL Editor do Supabase. É seguro rodar de novo.
-- Rode ANTES de atualizar o painel.

alter table perfil_usuario add column if not exists nome text;
