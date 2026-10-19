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
