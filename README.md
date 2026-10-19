# Radar de Leilões — Caixa

Sistema que monitora a lista pública e oficial de imóveis da Caixa Econômica Federal e
avisa quando aparece um imóvel que bate nos seus alertas. Cada pessoa tem o próprio login,
os próprios alertas, favoritos e avisos no Telegram.

> **Nunca escreva senhas, chaves ou tokens neste repositório** (nem neste README, nem em
> código, nem em commits). Se o repositório é público, tudo o que estiver nele é público,
> inclusive no histórico. Segredos ficam só nos *Secrets* do GitHub e do Supabase.

## O que o sistema faz

- **Alertas:** estado, cidade, bairros (opcional), faixa de valor de avaliação e filtros
  avançados (tipo, quartos, desconto, área, modalidade, financiamento).
- **Busca automática 3 vezes ao dia** (10h, 15h e 20h, horário de Brasília), sobre as listas
  oficiais da Caixa.
- **Painel web** com login: alertas, imóveis (com 1º e 2º leilão, edital, mapa), favoritos,
  "sem interesse", seleção em lote, agenda de leilões e calculadora de custo real.
- **Avisos no Telegram:** imóvel novo, mudança de preço ou de praça, lembrete na véspera do
  leilão e, se você preferir, um resumo único às 20h. Favoritos aparecem com ⭐.
- **Avisos de problema** para o administrador (busca falhou, incompleta ou voltou ao normal).

## Como funciona

```
Caixa (lista CSV + página de cada imóvel)
        │   3x ao dia
        ▼
GitHub Actions ── scripts/buscar_imoveis.py ──▶ Supabase (banco + login)
        │                                           ▲            │
        └── avisos ──▶ Telegram                     │            ▼
                          │                         │      Painel (GitHub Pages)
                          └── /start ──▶ Edge Function telegram-webhook
```

| Peça | Onde roda | Arquivo |
|---|---|---|
| Robô de busca e avisos | GitHub Actions (agendado) | `scripts/buscar_imoveis.py`, `.github/workflows/atualizar-imoveis.yml` |
| Banco e login | Supabase | `sql/*.sql` |
| Painel | GitHub Pages (arquivo único, no navegador) | `painel/index.html` |
| Conexão do Telegram na hora | Supabase Edge Function | `supabase/functions/telegram-webhook/index.ts` |

**O robô**, a cada execução: baixa o CSV de cada estado que aparece em algum alerta ativo,
aplica os alertas de todos os usuários, lê a página de detalhe (leilões, matrícula, edital,
nome do empreendimento, CEP) **só dos imóveis novos ou que mudaram de preço/modalidade**, grava
no Supabase e envia os avisos. A cada rodada ele refaz, para cada alerta, a lista de imóveis
que batem: o que deixou de bater some sozinho.

**Segurança dos dados:** o banco usa RLS: cada pessoa só lê e grava as próprias linhas
(alertas, favoritos, perfil, cálculos). A tabela `imoveis` é compartilhada (dado público da
Caixa). A chave `anon` que fica no painel é pública por natureza e não lê nada sem login. A
chave `service_role` só existe nos Secrets do GitHub e da Edge Function.

## Estrutura do repositório

```
.
├── README.md
├── requirements.txt
├── .github/workflows/atualizar-imoveis.yml   # agendamento (10h, 15h, 20h) e execução manual
├── scripts/buscar_imoveis.py                 # robô
├── painel/index.html                         # painel (HTML, CSS e JS num arquivo só)
├── supabase/functions/telegram-webhook/      # conexão instantânea do Telegram
│   └── index.ts
└── sql/
    ├── schema.sql                  # instalação do zero (inclui tudo abaixo)
    ├── migracao_multiusuario.sql   # só para quem veio da versão de um usuário
    ├── adicionar_telegram.sql      # tabela de perfil/Telegram (versões antigas)
    ├── migracao_v3.sql             # redesign, calculadora, resumo de status
    ├── liberar_acesso.sql          # liberações de acesso (ver "Problemas comuns")
    ├── migracao_v4.sql             # filtros avançados, resumo das 20h, código de uso único
    ├── migracao_v5.sql             # CEP do imóvel
    └── migracao_v6.sql             # nome de exibição de cada pessoa
```

## Usando o painel

- **Meus alertas** (tela inicial): cada alerta mostra quantos imóveis tem, quantos são novos
  e o próximo leilão. Dá para editar, pausar, retomar e excluir.
  - **Pausar** pergunta se os resultados do alerta ficam ou saem da lista. Favoritos e
    imóveis que também batem em outro alerta ativo seu nunca saem.
- **Imóveis:** abas *Todos*, *Favoritos* e *Sem interesse*. Marque vários com a caixa de
  seleção para agir em lote:
  - **Favoritar:** acompanha de perto (⭐ no Telegram).
  - **Sem interesse:** o imóvel não volta mais e não gera aviso; dá para restaurar na aba.
  - **Remover da lista:** some agora e só volta se o imóvel mudar de preço ou de praça.
  - Toda ação tem **Desfazer** por alguns segundos.
- **Mais filtros** (tipo, quartos, desconto, área, modalidade, financiamento) filtram a tela.
  Para valerem também nos **avisos**, salve-os dentro de um alerta ("Salvar estes critérios
  num alerta", ou "Filtros avançados" ao criar/editar um alerta).
- **Custo real:** lance, comissão do leiloeiro, ITBI, registro, débitos, reforma e
  desocupação, com resultado de revenda e de aluguel (retorno sobre o valor de mercado e
  sobre o capital investido). Os percentuais vêm de exemplo: confirme na prefeitura e no edital.
- **Ver no mapa:** envia nome do empreendimento, rua e número, bairro, cidade/UF e CEP
  (quando a Caixa informa). Apto, torre e bloco ficam de fora para o Google achar o prédio.
- **Menu da conta:** *Meu nome* (como você quer ser chamado), *Notificações no Telegram* e *Sair*.
- **Faixa de aviso no topo:** aparece se uma busca prevista passou de 2 horas sem rodar ou se
  a última falhou. Entre duas buscas de horário fixo não há alarme.

## Telegram

**Conectar (cada pessoa, no próprio login):** menu da conta → *Notificações no Telegram* →
escanear o QR Code → tocar em **Iniciar**. O QR Code leva um código aleatório de uso único,
válido por 15 minutos; o bot confirma na hora e a janela do painel vira "Conectado" sozinha.

**O que chega:**

| Aviso | Quando |
|---|---|
| Imóvel novo | a busca encontra um imóvel que bate num alerta seu |
| Mudou de praça ou de preço | um imóvel já conhecido muda de modalidade ou de valor |
| Leilão amanhã | só na busca das 20h; só favoritos ou todos, à sua escolha |
| Resumo do dia | opção "Resumo único às 20h": junta o que foi achado às 10h, 15h e 20h |

Imóveis marcados como *sem interesse* nunca geram aviso. Cada pessoa só recebe o que bate
nos **próprios** alertas.

**Avisos de problema (só administrador):** busca incompleta, busca que falhou e "voltou ao
normal" vão para o chat definido em `TELEGRAM_ADMIN_CHAT_ID`. O mesmo problema repetido não
gera aviso repetido.

## Instalação do zero

### 1. Supabase

1. Crie um projeto em [supabase.com](https://supabase.com) (região `South America`).
2. **SQL Editor** → *New query* → cole o conteúdo de `sql/schema.sql` → **Run**. Ele cria
   as tabelas, as regras de segurança e as liberações de acesso.
3. **Authentication → Users → Add user**: e-mail e uma **senha forte e só sua**, marcando
   *Auto Confirm User*. Um usuário por pessoa; não existe tela de cadastro público.
4. **Project Settings → API**: anote a *Project URL*, a chave *anon* e a *service_role*.

### 2. Painel

No começo do script de `painel/index.html`, ajuste estas constantes:

| Constante | O que colocar |
|---|---|
| `SUPABASE_URL_FIXO` | a *Project URL* |
| `SUPABASE_ANON_KEY_FIXO` | a chave *anon* (pública; **nunca** a `service_role`) |
| `TELEGRAM_BOT_USERNAME` | o @ do bot, sem o `@` (passo 4) |

O nome que aparece em "Olá, ..." **não fica no código** (o painel é público): cada pessoa o define
no menu da conta → **Meu nome**, e ele é guardado no perfil, no Supabase. Sem nome definido,
aparece o e-mail da própria pessoa.

### 3. GitHub

1. Suba todos os arquivos mantendo as pastas. **Cuidado com a pasta `.github`:** no
   Windows/Mac nomes que começam com ponto ficam ocultos e não sobem no "arrastar". Se
   sumir, crie o arquivo pelo GitHub (*Add file → Create new file*, nome
   `.github/workflows/atualizar-imoveis.yml`).
2. **Settings → Secrets and variables → Actions → Secrets:**

   | Secret | Valor |
   |---|---|
   | `SUPABASE_URL` | *Project URL* (sem `/rest/v1`) |
   | `SUPABASE_SERVICE_KEY` | chave *service_role* |
   | `TELEGRAM_BOT_TOKEN` | token do bot (passo 4) |
   | `TELEGRAM_ADMIN_CHAT_ID` | seu chat_id, para os avisos de problema (opcional) |

   Em **Variables**, opcional: `PAINEL_URL` com o endereço do painel (usado nos botões
   "Ver no Radar" das mensagens).
3. **Settings → Pages → Deploy from a branch → `main` / `(root)`**. O painel fica em
   `https://SEU-USUARIO.github.io/SEU-REPO/painel/` (com a barra `/painel/` no fim).

### 4. Bot do Telegram

1. No Telegram, fale com **@BotFather** → `/newbot`. Guarde o **token** e o **@username**.
   Crie um bot só para este sistema: dois programas lendo o mesmo bot se atrapalham.
2. Coloque o token no Secret `TELEGRAM_BOT_TOKEN` e o @username em `TELEGRAM_BOT_USERNAME`.

### 5. Conexão instantânea (Edge Function)

Sem esta etapa o QR Code só confirmaria na próxima busca do robô.

1. Supabase → **Edge Functions** → criar função chamada exatamente `telegram-webhook`,
   colar o conteúdo de `supabase/functions/telegram-webhook/index.ts` e publicar.
2. Na aba *Settings* da função, **desligar "Verify JWT"** (o Telegram não envia JWT; a
   proteção é a senha do passo 3).
3. **Edge Functions → Secrets:** `TELEGRAM_BOT_TOKEN` (o mesmo do GitHub) e
   `TELEGRAM_WEBHOOK_SECRET` (uma senha inventada por você: 40+ caracteres, só letras,
   números, `_` e `-`).
4. Abra no navegador, trocando `TOKEN`, `SENHA` e `SEU-PROJETO`:
   ```
   https://api.telegram.org/botTOKEN/setWebhook?url=https://SEU-PROJETO.supabase.co/functions/v1/telegram-webhook&secret_token=SENHA
   ```
   Deve responder `"Webhook was set"`. **Não compartilhe esse endereço**: ele contém o token.
5. Conferir: `https://api.telegram.org/botTOKEN/getWebhookInfo` mostra a `url` e, se algo
   falhar, `last_error_message`. Para desligar: `.../deleteWebhook` (o robô volta ao método
   antigo, que lê as mensagens a cada busca).

### 6. Primeira busca

**Actions → Atualizar Radar de Leilões Caixa → Run workflow.** Sem alertas cadastrados, o
log diz "Nenhum filtro ativo": é esperado. Entre no painel, crie um alerta e rode de novo.

## Operação

- **Rodar na hora:** *Actions → Run workflow*.
- **Reler a página de todos os imóveis:** *Run workflow* marcando "Reler a página de detalhe
  de TODOS os imóveis". Use uma vez depois de uma melhoria na leitura (nome do empreendimento,
  CEP...). Não gera avisos para quem já estava salvo.
- **Novo usuário:** *Authentication → Users → Add user*. Ele entra no mesmo link, define o
  nome em *Meu nome* e cadastra os próprios alertas.
- **Ordem ao atualizar:** (1) rodar a migração SQL nova, (2) atualizar a Edge Function se
  mudou, (3) editar `scripts/buscar_imoveis.py` e `painel/index.html` no GitHub, (4)
  **Ctrl+F5** no painel. O banco vem primeiro: robô ou painel novos sem a coluna nova
  quebram. No GitHub, **edite** o arquivo (lápis → Ctrl+A → colar → *Commit changes*); não
  apague, para não perder a pasta.

### Atualizando uma instalação antiga

Rode no SQL Editor, na ordem, só o que ainda não rodou (todos podem rodar de novo):

| Arquivo | Para quê |
|---|---|
| `migracao_multiusuario.sql` | vinda da versão de um usuário só (pede o seu `user_id` em dois pontos) |
| `adicionar_telegram.sql` | tabela do perfil/Telegram |
| `migracao_v3.sql` | redesign, calculadora, status das buscas |
| `liberar_acesso.sql` | liberações de acesso (ver abaixo) |
| `migracao_v4.sql` | filtros avançados, resumo único, código de uso único no QR Code |
| `migracao_v5.sql` | CEP (recria a view `meus_imoveis`) |
| `migracao_v6.sql` | nome de exibição de cada pessoa |

## Problemas comuns

| O que aparece | O que é | O que fazer |
|---|---|---|
| Faixa "Parte dos dados não carregou… *permission denied*" | desde 2026 o Supabase não libera tabela nova para o painel sozinho | rodar `sql/liberar_acesso.sql` |
| Faixa com "JWT issued at future" ou erro de rede | relógio entre servidores / rede, passageiro | o painel já tenta de novo sozinho; se aparecer, **F5** |
| Erro "column … does not exist" no log do robô ou ao salvar | migração SQL não rodada | rodar a migração na tabela acima |
| QR Code não confirma | a Edge Function ou o webhook não estão ligados | `getWebhookInfo` (veja `last_error_message`: 401 = senha diferente nos dois lugares; 404 = nome da função errado) |
| "Esse link expirou ou já foi usado" | o QR Code vale 15 min e só uma vez | gerar de novo na janela do Telegram |
| Faixa "A busca das 10h ainda não apareceu" | uma busca prevista passou de 2 h sem rodar (o GitHub às vezes atrasa) | esperar; se persistir, *Actions* → ver o log ou rodar manualmente |
| Pasta `.github` ou `scripts` sumiu no GitHub | arquivo foi apagado em vez de editado; o Git não guarda pasta vazia | recriar o arquivo (*Add file → Create new file* com o caminho completo) |
| Painel antigo depois de atualizar | cache do navegador | **Ctrl+F5**, depois de ~1 minuto da publicação |

## Limitações

- A Caixa pode mudar o layout do CSV ou das páginas de detalhe sem aviso. O robô detecta as
  colunas do CSV pelo nome e tolera pequenas mudanças; mudanças maiores exigem ajuste.
- Valores que o sistema mostra vêm da Caixa e podem estranhar (em leilão SFI o 2º leilão pode
  ser maior que o 1º). Confira sempre no site oficial.
- O desconto usado nos **avisos** parte do preço atual do CSV; o painel usa a praça vigente.
- O GitHub pode atrasar as buscas agendadas e desativa agendamentos de repositórios sem
  nenhuma atividade por muito tempo.
- Os percentuais da calculadora (comissão, ITBI) são exemplos editáveis, não regra.
- Sempre confirme edital, ocupação, débitos e condições de financiamento no site oficial da
  Caixa antes de qualquer decisão. Este sistema é uma ferramenta de descoberta e não
  substitui a checagem oficial.
