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
grant select, insert, update, delete on public.prazo_imovel      to authenticated;

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
grant select, insert, update, delete on public.prazo_imovel        to service_role;
grant select, insert, update, delete on public.aviso_prazo_enviado to service_role;
