-- Migração v8: de onde veio cada prazo (lido da página da Caixa, ou informado à mão).
-- Quando a página da Caixa traz a data (licitação aberta), o robô a grava sozinho com origem 'caixa'.
-- Se alguém corrigir à mão, a origem vira 'manual' e o robô nunca mais sobrescreve.
-- Rode UMA VEZ no SQL Editor do Supabase. É seguro rodar de novo.
-- Rode ANTES de atualizar o painel e o robô.

alter table prazo_imovel add column if not exists origem text not null default 'manual';   -- manual | caixa
