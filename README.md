# Radar de Leilões — Caixa

Sistema que monitora a lista pública e oficial de imóveis da Caixa Econômica
Federal e avisa quando surge um imóvel que bate em algum dos seus filtros
salvos (estado, cidade, bairros e faixa de valor de avaliação).

**Nesta fase:** o site já fica no ar, funcional, com login, cadastro de
filtros e resultados atualizados automaticamente todo dia — **sem Telegram
ainda**. O aviso por Telegram entra numa etapa seguinte, depois que você
confirmar que o site está funcionando do jeito que você quer.

## Como funciona

1. Você cadastra "alertas" (filtros) no painel web, logado com seu e-mail e senha.
2. Uma vez por dia, o GitHub Actions roda `scripts/buscar_imoveis.py`, que:
   - baixa o CSV oficial de cada UF que aparece em algum filtro ativo;
   - aplica os filtros sobre os imóveis;
   - busca a página de detalhe (1º/2º leilão, matrícula, edital...) só dos
     imóveis novos ou que mudaram de modalidade/preço desde a última vez —
     os que não mudaram reaproveitam o que já tinha, sem sobrecarregar o
     site da Caixa;
   - grava tudo no Supabase.
3. O painel web lê o Supabase direto do navegador — é onde você cadastra
   alertas, vê os resultados (agrupados por bairro, com situação de cada
   leilão, riscando o que já passou) e marca favoritos.

---

## Passo a passo pra colocar no ar

### 1. Criar o projeto no Supabase

1. Acesse [supabase.com](https://supabase.com) e crie uma conta (dá pra
   entrar com GitHub).
2. **New Project** → escolha um nome (ex: `radar-leiloes`), uma senha de
   banco (guarde essa senha em local seguro, mas ela não é usada em nenhum
   lugar do nosso sistema depois de criada) e a região mais próxima
   (`South America (São Paulo)`).
3. Espere o projeto terminar de provisionar (1-2 minutos).

### 2. Rodar o schema do banco

1. No menu lateral do projeto, clique em **SQL Editor**.
2. **New query**.
3. Abra o arquivo `sql/schema.sql` (está no pacote que te entreguei), copie
   todo o conteúdo, cole no editor e clique em **Run**.
4. Deve aparecer "Success. No rows returned" — isso confirma que as tabelas
   `filtros`, `imoveis` e `historico` foram criadas.

### 3. Criar seu usuário de login

1. No menu lateral, **Authentication** → **Users** → **Add user** →
   **Create new user**.
2. **E-mail**: use o seu e-mail de verdade (recomendado, caso precise
   recuperar a senha depois).
3. **Password**: `99554829`
4. Marque **Auto Confirm User** (senão ele espera uma confirmação por
   e-mail que não vai chegar, já que não configuramos envio de e-mail).
5. **Create user**.

Guarde esse e-mail — é o que você vai digitar no painel pra entrar (junto
com a senha `99554829`).

### 4. Pegar as chaves do projeto

No menu lateral, **Project Settings** (ícone de engrenagem) → **API**.
Anote três valores, você vai precisar deles no passo 6 e no primeiro login:

- **Project URL** — algo como `https://xxxxxxxxxxxx.supabase.co`
- **anon public** key — uma chave longa, começa com `eyJ...` — vai no
  navegador, é pública por natureza (protegida pelo RLS que já vem no
  schema: sem login, ela não lê nem escreve nada)
- **service_role** key — outra chave longa — **essa é secreta**, só vai
  num Secret do GitHub (passo 6), nunca no navegador nem em nenhum arquivo
  do repositório

### 5. Subir o código pro GitHub

1. Crie um repositório novo no GitHub (pode ser privado ou público — como
   os dados de imóvel são públicos por natureza, não tem problema deixar
   público; se preferir privado, funciona igual).
2. Suba todos os arquivos do pacote (`sql/`, `scripts/`, `painel/`,
   `.github/`, `requirements.txt`, `README.md`) mantendo essa mesma
   estrutura de pastas. Pode ser pela interface web do GitHub
   ("Add file" → "Upload files", arraste a pasta inteira) ou por linha de
   comando com `git`, o que for mais confortável pra você.

### 6. Configurar os Secrets

1. No repositório, **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret**, duas vezes:
   - Nome `SUPABASE_URL`, valor a Project URL do passo 4.
   - Nome `SUPABASE_SERVICE_KEY`, valor a chave `service_role` do passo 4.

### 7. Rodar a primeira busca manualmente

Não precisa esperar o agendamento das 08:15 pra testar:

1. No repositório, aba **Actions**.
2. Clique no workflow **Atualizar Radar de Leilões Caixa** na lista à
   esquerda.
3. Botão **Run workflow** (canto direito) → **Run workflow** de novo pra
   confirmar.
4. Acompanhe a execução clicando nela — deve levar de alguns segundos a
   poucos minutos, dependendo de quantos imóveis baterem nos seus filtros
   (a primeira vez busca detalhe de todo mundo; nas próximas só de quem for
   novo ou mudar).

Se der erro, clique na execução falha pra ver o log — a mensagem costuma
dizer exatamente o que faltou (secret não configurado, erro de rede, etc.).

**Atenção**: como ainda não existe nenhum filtro cadastrado (você vai
cadastrar no painel, passo 9), essa primeira execução provavelmente só vai
mostrar "Nenhum filtro ativo cadastrado no Supabase. Nada a fazer." — é
esperado, não é erro.

### 8. Publicar o painel (GitHub Pages)

1. No repositório, **Settings** → **Pages**.
2. Em **Source**, escolha **Deploy from a branch**.
3. Em **Branch**, escolha `main` (ou a branch onde você subiu o código) e
   pasta **/ (root)** → **Save**.
4. Espere um minuto e recarregue a página — vai aparecer um link parecido
   com `https://SEUUSUARIO.github.io/NOMEDOREPO/`.
5. Como o painel está na pasta `painel/`, o endereço final é:
   `https://SEUUSUARIO.github.io/NOMEDOREPO/painel/`
   (repare na barra `/painel/` no final — sem ela, cai numa página em
   branco ou 404, porque o `index.html` não está na raiz do repositório).

### 9. Primeiro acesso e cadastro de filtros

1. Abra `https://SEUUSUARIO.github.io/NOMEDOREPO/painel/`.
2. Preencha **URL do projeto Supabase** e **Chave anon** (passo 4) e o
   **e-mail**/**senha** (`99554829`) do passo 3 → **Entrar**.
3. A URL e a chave ficam salvas no seu navegador — não precisa digitar de
   novo nas próximas vezes; a sessão de login também fica salva.
4. Na aba **Filtros**, cadastre seus alertas (nome, estado, cidade, bairros
   opcionais, valor mínimo/máximo).
5. Espere a próxima execução automática (08:15) ou rode manualmente de
   novo (passo 7) — os resultados aparecem na aba **Resultados**.

---

## Estrutura

```
caixa-leiloes/
├── README.md
├── requirements.txt
├── sql/schema.sql                          # tabelas do Supabase
├── scripts/buscar_imoveis.py               # roda todo dia no GitHub Actions
├── .github/workflows/atualizar-imoveis.yml # agendamento
└── painel/index.html                       # painel web (login + filtros + resultados + favoritos)
```

## O que ainda falta (próxima etapa)

- **Telegram**: avisos automáticos de imóvel novo/alterado. Combinamos
  deixar o site funcionando primeiro — me avisa quando quiser seguir pra
  essa parte.
- **Rodar mais de uma vez ao dia**: o workflow está com um horário só
  (08:15). É só adicionar mais linhas de `cron` no
  `.github/workflows/atualizar-imoveis.yml` quando quiser.

## Limitações conhecidas

- A Caixa pode mudar o layout do CSV ou da página de detalhe sem aviso — o
  script tolera pequenas mudanças (detecta colunas pelo nome, não pela
  posição), mas mudanças maiores podem exigir ajuste.
- O filtro de bairro faz comparação textual — bairros com nomes muito
  genéricos podem gerar falsos positivos ocasionais.
- Sempre confirme edital, ocupação, débitos e condições de financiamento
  direto no site oficial da Caixa antes de qualquer decisão — este sistema
  é só um filtro de descoberta, não substitui a checagem oficial.
