#!/usr/bin/env python3
"""
Radar de Leilões Caixa — roda no GitHub Actions
--------------------------------------------------
Baixa o CSV oficial da Caixa, aplica os filtros ativos cadastrados no
Supabase (tabela `filtros`), busca a página de detalhe só de quem é novo ou
mudou de situação, e grava tudo no Supabase (tabelas `imoveis` e
`historico`). O painel web (painel/index.html) lê esses dados direto do
Supabase — este script não gera nenhuma página.

Avisa no Telegram: imóveis novos, mudanças de praça/preço, lembretes na
véspera do leilão (com destaque pros favoritos) e, pro administrador,
problemas na própria busca.

Variáveis de ambiente esperadas (Secrets do GitHub Actions):
    SUPABASE_URL           ex: https://xxxxx.supabase.co
    SUPABASE_SERVICE_KEY   chave service_role (nunca a anon — precisa
                            ignorar RLS pra gravar em `imoveis`/`historico`)
    TELEGRAM_BOT_TOKEN     opcional — sem ele, não manda aviso nenhum
    TELEGRAM_ADMIN_CHAT_ID opcional — chat que recebe os avisos de problema
    PAINEL_URL             opcional — link do painel usado nos botões

Uso:
    python buscar_imoveis.py
    python buscar_imoveis.py --sem-detalhes   # mais rápido, sem página individual
"""

from __future__ import annotations

import argparse
import csv
import html as html_module
import io
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_{uf}.csv"
DETALHE_URL = "https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp?hdnOrigem=index&hdnimovel={numero}"


def normalize(value) -> str:
    value = str(value or "")
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = value.replace("º", "").replace("°", "")
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value)
    return value.strip().lower()


def parse_decimal(value) -> float | None:
    text = str(value or "").strip().replace("R$", "").replace("%", "").replace(" ", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def decode_bytes(raw: bytes) -> str:
    for enc in ("cp1252", "latin1", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("codificação do CSV não reconhecida")


def download_uf(uf: str, retries: int = 3) -> str:
    url = CSV_URL.format(uf=uf)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/csv,application/octet-stream,*/*",
    }
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            print(f"Baixando {url} (tentativa {attempt})...")
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            if len(resp.content) < 100:
                raise RuntimeError("arquivo vazio")
            return decode_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < retries:
                time.sleep(5 * attempt)
    raise RuntimeError(f"falha ao baixar CSV de {uf}: {last_err}")


def find_header(lines: list[str]) -> int:
    for idx, line in enumerate(lines[:40]):
        try:
            cells = [normalize(c) for c in next(csv.reader([line], delimiter=";"))]
        except StopIteration:
            continue
        if any("imovel" in c for c in cells) and "cidade" in cells:
            return idx
    raise RuntimeError("cabeçalho do CSV não reconhecido (layout pode ter mudado)")


def col_index(headers: list[str], predicate) -> int:
    for idx, h in enumerate(normalize(h) for h in headers):
        if predicate(h):
            return idx
    return -1


def get(row: list[str], idx: int) -> str:
    return row[idx].strip() if 0 <= idx < len(row) else ""


def parse_properties(text: str, uf: str) -> list[dict]:
    lines = text.splitlines()
    header_idx = find_header(lines)
    reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])), delimiter=";")
    headers = next(reader)

    idx = {
        "numero": col_index(headers, lambda h: "imovel" in h and ("numero" in h or h.startswith("n "))),
        "uf": col_index(headers, lambda h: h == "uf"),
        "cidade": col_index(headers, lambda h: h == "cidade"),
        "bairro": col_index(headers, lambda h: h == "bairro"),
        "endereco": col_index(headers, lambda h: "endereco" in h),
        "preco": col_index(headers, lambda h: h == "preco"),
        "avaliacao": col_index(headers, lambda h: "valor" in h and "avaliacao" in h),
        "desconto": col_index(headers, lambda h: "desconto" in h),
        "descricao": col_index(headers, lambda h: "descricao" in h),
        "modalidade": col_index(headers, lambda h: "modalidade" in h),
        "link": col_index(headers, lambda h: "link" in h),
        "financiamento": col_index(headers, lambda h: h == "financiamento"),
    }
    if idx["numero"] < 0 or idx["cidade"] < 0:
        raise RuntimeError(f"{uf}: estrutura do CSV mudou, colunas encontradas: {headers}")

    result = []
    vistos = set()
    for row in reader:
        numero = get(row, idx["numero"])
        cidade = get(row, idx["cidade"])
        if not numero or not cidade or numero in vistos:
            continue
        vistos.add(numero)
        result.append({
            "numero_imovel": numero,
            "uf": (get(row, idx["uf"]) or uf).upper(),
            "cidade": cidade,
            "bairro": get(row, idx["bairro"]),
            "endereco": get(row, idx["endereco"]),
            "valor_avaliacao": parse_decimal(get(row, idx["avaliacao"])),
            "preco": parse_decimal(get(row, idx["preco"])),
            "desconto": get(row, idx["desconto"]),
            "descricao": get(row, idx["descricao"]),
            "modalidade": get(row, idx["modalidade"]),
            "financiamento": (normalize(get(row, idx["financiamento"])).startswith("s")
                              if idx["financiamento"] >= 0 and get(row, idx["financiamento"]) else None),
            "link": get(row, idx["link"]) or (
                f"https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp"
                f"?hdnOrigem=index&hdnimovel={re.sub(r'[^0-9]', '', numero)}"
            ),
        })
    return result


def parse_descricao_estruturada(descricao: str) -> dict:
    """Extrai tipo/quartos/vagas/área/amenidades/IPTU/matrícula do texto livre da
    coluna Descrição — que varia de formato entre imóveis (às vezes por extenso
    "2 quarto(s)", às vezes abreviado "2 qts", "a.serv", "sl")."""
    texto = str(descricao or "")
    tipo = ""
    if texto:
        primeiro = texto.split(",", 1)[0].strip()
        if primeiro and len(primeiro) <= 45 and not re.search(r"\d", primeiro):
            tipo = primeiro

    def numero(padroes: list[str]) -> int | None:
        for p in padroes:
            m = re.search(p, texto, re.I)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        return None

    def area(padrao: str) -> float | None:
        m = re.search(padrao, texto, re.I)
        return parse_decimal(m.group(1)) if m else None

    def texto_campo(padrao: str) -> str | None:
        m = re.search(padrao, texto, re.I)
        return m.group(1).strip() if m else None

    banheiros = numero([r"(\d+)\s*wc(?:s)?\b", r"(\d+)\s*banheiro(?:s)?"])
    if banheiros is None and re.search(r"\bwc\b", texto, re.I):
        banheiros = 1

    salas = numero([r"(\d+)\s*sala\(s\)", r"(\d+)\s*sl\b"])
    if salas is None and re.search(r"\bsl\b", texto, re.I):
        salas = 1

    # amenidades sem contagem — só marca presença, cada uma vira um chip próprio
    amenidades = []
    for padrao, rotulo in [
        (r"\ba\.?\s*serv\b", "Área de serviço"),
        (r"\bcozinha\b", "Cozinha"),
        (r"\bvaranda\b|\bsacada\b", "Varanda/sacada"),
        (r"\blavabo\b", "Lavabo"),
        (r"\bdce\b", "DCE"),
    ]:
        if re.search(padrao, texto, re.I):
            amenidades.append(rotulo)

    return {
        "tipo": tipo,
        "quartos": numero([r"(\d+)\s*qto\(s\)", r"(\d+)\s*qts?\b", r"(\d+)\s*quarto(?:s)?"]),
        "banheiros": banheiros,
        "salas": salas,
        "vagas": numero([r"(\d+)\s*vaga\(s\)"]),
        "area_total": area(r"([\d.,]+)\s*m?2?\s*de\s+[aá]rea\s+total"),
        "area_privativa": area(r"([\d.,]+)\s*m?2?\s*de\s+[aá]rea\s+privativa"),
        "area_terreno": area(r"([\d.,]+)\s*m?2?\s*de\s+[aá]rea\s+(?:do\s+)?terreno"),
        "amenidades": amenidades,
        "iptu": texto_campo(r"IPTU[:\s]*([\d./\-]+)"),
        "matricula": texto_campo(r"Matr[íi]cula(?:\(s\))?[:\s]*([\d./\-]+)"),
    }


def html_para_texto(pagina_html: str) -> str:
    """Achata o HTML bruto pra texto simples, tolerante à estrutura exata das tags —
    a ideia é procurar pelos RÓTULOS da página (ex: 'Data do 1º Leilão'), não pela
    posição das tags, já que eu não tenho certeza de como o HTML bruto é montado."""
    texto = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", pagina_html)
    texto = re.sub(r"(?i)<br\s*/?>", "\n", texto)
    texto = re.sub(r"(?i)</(p|div|li|tr|h\d|section)>", "\n", texto)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html_module.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto


def _texto_do_titulo(trecho: str) -> str | None:
    """Texto que vem logo depois do primeiro <h1..h6> do trecho, até a próxima tag
    de QUALQUER tipo. Na página da Caixa o <h5> do nome é seguido de dezenas de
    <input type="hidden"> antes do </h5>, então não dá pra exigir só texto até o fechamento."""
    m = re.search(r"<h[1-6][^>]*>\s*([^<]{2,100}?)\s*(?:<|$)", trecho, re.I)
    if not m:
        return None
    nome = re.sub(r"\s+", " ", html_module.unescape(m.group(1))).strip()
    if not nome or not re.search(r"[A-Za-zÀ-ÿ]", nome):
        return None
    return nome


# campos que só vêm da página de detalhe (o CSV não traz)
CAMPOS_DETALHE = ("valor_1_leilao", "data_1_leilao", "valor_2_leilao", "data_2_leilao",
                  "matricula", "inscricao_imobiliaria", "comarca", "edital", "leiloeiro",
                  "foto_url", "numero_formatado", "empreendimento", "cep")

# o <h5> também aparece em páginas de erro ("Nenhum imóvel encontrado...", sistema fora do ar):
# isso nunca pode virar nome de empreendimento
TITULOS_QUE_NAO_SAO_NOME = re.compile(r"nenhum|n[aã]o encontrad|encontrad[oa]s? para|indispon|erro|tente novamente|manuten", re.I)


def extrair_titulo_empreendimento(html_bruto: str) -> str | None:
    """O nome do empreendimento é o título (<h5>) no topo do bloco de dados do imóvel
    (<div id="dadosImovel">), sem rótulo. Imóvel sem nome fica com o título vazio e
    devolve None, e a informação é omitida. Só vale em página de detalhe de verdade
    (a de erro não tem "Valor de avaliação")."""
    if "valor de avalia" not in html_bruto.lower():
        return None
    nome = _extrair_titulo(html_bruto)
    if nome and TITULOS_QUE_NAO_SAO_NOME.search(nome):
        return None
    return nome


def _extrair_titulo(html_bruto: str) -> str | None:
    pos = html_bruto.find('id="dadosImovel"')
    if pos != -1:
        return _texto_do_titulo(html_bruto[pos:pos + 3000])
    # página fora do padrão: pega o título mais próximo antes de "Valor de avaliação"
    pos_valor = html_bruto.lower().find("valor de avalia")
    if pos_valor == -1:
        return None
    candidatos = [m.start() for m in re.finditer(r"<h[1-6][^>]*>", html_bruto[:pos_valor], re.I)]
    for inicio in reversed(candidatos):
        nome = _texto_do_titulo(html_bruto[inicio:pos_valor])
        if nome:
            return nome
    return None


def extrair_cep(texto: str) -> str | None:
    """CEP do imóvel, no formato 12345-678. Na página da Caixa ele vem dentro da linha do
    endereço ("... RIO VERMELHO - CEP: 41940-220, SALVADOR - BAHIA"); não existe no CSV.
    Só vale o CEP que está no bloco do "Endereço:" (antes da "Descrição"): um CEP de outra
    parte da página nunca é aceito, porque um CEP errado no mapa é pior que nenhum."""
    m = re.search(r"Endere[çc]o\s*:(?:(?!Descri[çc][ãa]o).){0,500}?CEP\s*:?\s*(\d{5})[-\s.]?(\d{3})", texto, re.I | re.S)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def buscar_detalhe(numero_imovel: str) -> dict:
    """Busca a página individual do imóvel e tenta extrair 1º/2º leilão, matrícula,
    comarca, edital, leiloeiro e foto. Best-effort: campo que não achar fica None."""
    digitos = re.sub(r"[^0-9]", "", numero_imovel)
    if not digitos:
        return {}
    url = DETALHE_URL.format(numero=digitos)
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {"_erro_detalhe": str(exc)}

    texto = html_para_texto(resp.text)

    def buscar(padrao: str, grupo: int = 1):
        m = re.search(padrao, texto, re.I)
        return m.group(grupo).strip() if m else None

    def buscar_qualquer(padroes: list[str], grupo: int = 1):
        for p in padroes:
            m = re.search(p, texto, re.I)
            if m:
                return m.group(grupo).strip()
        return None

    # a data para de capturar antes do próximo rótulo conhecido (ou 2+ espaços/nova
    # linha), em vez de um número fixo de caracteres — tolera formatos diferentes
    # de hora ("10h00", "10:00" etc.) sem vazar pro campo seguinte.
    limite_data = r"(?=\s{2,}|Endere[çc]o|Descri[çc][ãa]o|Matr[íi]cula|Data do|\d[ªa]\s*Pra[çc]a|R\$|$)"

    def padrao_data(rotulos: list[str]) -> list[str]:
        return [rf"{r}[^\d]{{0,20}}(\d{{2}}/\d{{2}}/\d{{4}}[^\n]{{0,20}}?){limite_data}" for r in rotulos]

    foto = re.search(r'src=["\']?(https://venda-imoveis\.caixa\.gov\.br/fotos/[^\s"\')]+\.jpg)', resp.text, re.I)

    return {
        "numero_formatado": buscar(r"N[úu]mero do im[óo]vel[:\s]*([\d\-]+)"),
        "cep": extrair_cep(texto),
        "empreendimento": extrair_titulo_empreendimento(resp.text) or buscar_qualquer([
            r"Empreendimento[:\s]*([^\n]{1,80}?)\s+(?:Tipo de im[óo]vel|Quartos|Matr[íi]cula|Comarca|Valor de avalia|$)",
            r"Nome do empreendimento[:\s]*([^\n]{1,80}?)\s+(?:Tipo de im[óo]vel|Quartos|Matr[íi]cula|Comarca|Valor de avalia|$)",
        ]),
        "valor_1_leilao": parse_decimal(buscar_qualquer([
            r"Data do 1[ºo] Leil[ãa]o[^R]{0,60}R\$\s*([\d.,]+)",
            r"1[ºo]\s*Leil[ãa]o[^R]{0,40}R\$\s*([\d.,]+)",
            r"1[ªa]\s*Pra[çc]a[^R]{0,60}R\$\s*([\d.,]+)",
        ])),
        "data_1_leilao": buscar_qualquer(padrao_data([r"Data do 1[ºo] Leil[ãa]o", r"1[ªa]\s*Pra[çc]a"])),
        "valor_2_leilao": parse_decimal(buscar_qualquer([
            r"Data do 2[ºo] Leil[ãa]o[^R]{0,60}R\$\s*([\d.,]+)",
            r"2[ºo]\s*Leil[ãa]o[^R]{0,40}R\$\s*([\d.,]+)",
            r"2[ªa]\s*Pra[çc]a[^R]{0,60}R\$\s*([\d.,]+)",
        ])),
        "data_2_leilao": buscar_qualquer(padrao_data([r"Data do 2[ºo] Leil[ãa]o", r"2[ªa]\s*Pra[çc]a"])),
        "matricula": buscar(r"Matr[íi]cula\(s\)[:\s]*([^\n]{1,60}?)\s+Comarca"),
        "inscricao_imobiliaria": buscar(r"Inscri[çc][ãa]o imobili[áa]ria[:\s]*([^\n]{1,40}?)\s+Averba")
            or buscar(r"\bIPTU[:\s]*([^\n]{1,40}?)(?:\s{2,}|\n)"),
        "comarca": buscar(r"Comarca[:\s]*([^\n]{1,60}?)\s+Of[íi]cio"),
        "edital": buscar(r"\bEdital[:\s]*([^\n]{1,40}?)\s+N[úu]mero do item"),
        "leiloeiro": buscar(r"Leiloeiro\(a\)[:\s]*([^\n]{1,60}?)\s+Data do 1"),
        "foto_url": foto.group(1) if foto else None,
    }


def link_mapa(endereco: str, bairro: str, cidade: str, uf: str) -> str:
    from urllib.parse import quote
    q = quote(f"{endereco}, {bairro}, {cidade} - {uf}")
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def categoria_tipo(tipo: str | None) -> str:
    t = normalize(tipo)
    if "apart" in t:
        return "Apartamento"
    if "casa" in t or "sobrado" in t:
        return "Casa"
    if "terreno" in t or "lote" in t or "gleba" in t:
        return "Terreno"
    return "Comercial e outros"


def grupo_modalidade(modalidade: str | None) -> str:
    t = normalize(modalidade)
    if "leil" in t:
        return "leilao"
    if "licita" in t:
        return "licitacao"
    return "direta"


def passa_criterios(im: dict, crit: dict | None) -> bool:
    """Critérios avançados do alerta (mesma lógica dos "Mais filtros" do painel).
    Sem critérios, tudo passa. O desconto usa o preço atual do CSV da Caixa."""
    if not crit:
        return True
    fatos = im.get("fatos") or {}
    tipos = crit.get("tipos") or []
    if tipos and categoria_tipo(fatos.get("tipo")) not in tipos:
        return False
    quartos = crit.get("quartos")
    if quartos and (fatos.get("quartos") or 0) < quartos:
        return False
    area = crit.get("area")
    if area and (fatos.get("area_privativa") or fatos.get("area_total") or 0) < area:
        return False
    desconto = crit.get("desconto")
    if desconto:
        av, preco = im.get("valor_avaliacao"), im.get("preco")
        if not av or preco is None or (av - preco) / av * 100 < desconto:
            return False
    modalidades = crit.get("modalidades") or []
    if modalidades and grupo_modalidade(im.get("modalidade")) not in modalidades:
        return False
    if crit.get("financiamento") and im.get("financiamento") is not True:
        return False
    return True


def imovel_bate(imovel: dict, cidade: str, bairros: list[str], valor_min: float, valor_max: float,
                criterios: dict | None = None) -> bool:
    if normalize(imovel["cidade"]) != normalize(cidade):
        return False
    if bairros:
        bairro_imovel = normalize(imovel.get("bairro"))
        if not bairro_imovel or not any(
            normalize(b) in bairro_imovel or bairro_imovel in normalize(b) for b in bairros
        ):
            return False
    valor = imovel.get("valor_avaliacao")
    if valor is None or not (valor_min <= valor <= valor_max):
        return False
    if criterios:
        if "fatos" not in imovel:
            imovel["fatos"] = parse_descricao_estruturada(imovel.get("descricao"))
        return passa_criterios(imovel, criterios)
    return True


def formatar_valor(v) -> str:
    if v is None:
        return "-"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
for _sufixo in ("/rest/v1", "/rest"):
    if SUPABASE_URL.endswith(_sufixo):
        SUPABASE_URL = SUPABASE_URL[: -len(_sufixo)]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # opcional — sem isso, só não avisa
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")  # opcional — avisos de problema
PAINEL_URL = (os.environ.get("PAINEL_URL") or "https://tdmascarenhas.github.io/leiloes_caixa/painel/").strip()
FUSO_BRASILIA = timezone(timedelta(hours=-3))
HEADERS_SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def carregar_filtros_ativos() -> list[dict]:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/filtros",
        headers=HEADERS_SB,
        params={"ativo": "eq.true", "select": "*"},
        timeout=30,
    )
    resp.raise_for_status()
    filtros = []
    for f in resp.json():
        filtros.append({
            "id": f["id"],
            "user_id": f["user_id"],
            "nome": f["nome"],
            "estado": f["estado"].upper(),
            "cidade": f["cidade"],
            "bairros": f.get("bairros") or [],
            "valor_min": f.get("valor_min") or 0,
            "valor_max": f["valor_max"] if f.get("valor_max") is not None else float("inf"),
            "criterios": f.get("criterios") or {},
        })
    return filtros


def carregar_estado_atual(ufs: list[str]) -> dict[str, dict]:
    """Estado salvo na execução anterior, só das UFs que interessam agora."""
    lista_uf = ",".join(ufs)
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/imoveis",
        headers=HEADERS_SB,
        params={"uf": f"in.({lista_uf})", "select": "*"},
        timeout=60,
    )
    resp.raise_for_status()
    return {f"{row['uf']}:{row['numero_imovel']}": row for row in resp.json()}


def upsert_imoveis(lote: list[dict]) -> None:
    """Grava um lote de imóveis. Como 'favorito' e 'primeira_vez_visto' não
    entram no payload, o upsert nunca sobrescreve esses dois campos em quem
    já existia — só em INSERT novo é que os defaults da tabela entram em
    jogo (favorito=false, primeira_vez_visto=now())."""
    if not lote:
        return
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/imoveis",
        headers={**HEADERS_SB, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "uf,numero_imovel"},
        json=lote,
        timeout=60,
    )
    if not resp.ok:
        print(f"ERRO no upsert em lote: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()


def buscar_ids_imoveis(ufs: list[str]) -> dict[str, str]:
    """chave 'UF:numero_imovel' -> id (uuid) — usado pra montar filtro_imovel
    depois que os imóveis já foram gravados/atualizados."""
    lista_uf = ",".join(ufs)
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/imoveis",
        headers=HEADERS_SB,
        params={"uf": f"in.({lista_uf})", "select": "id,uf,numero_imovel"},
        timeout=60,
    )
    resp.raise_for_status()
    return {f"{row['uf']}:{row['numero_imovel']}": row["id"] for row in resp.json()}


def limpar_associacoes_filtro(filtro_id: str) -> None:
    resp = requests.delete(
        f"{SUPABASE_URL}/rest/v1/filtro_imovel",
        headers=HEADERS_SB,
        params={"filtro_id": f"eq.{filtro_id}"},
        timeout=30,
    )
    if not resp.ok:
        print(f"ERRO ao limpar associações do filtro {filtro_id}: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()


def inserir_associacoes_filtro(lote: list[dict]) -> None:
    if not lote:
        return
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/filtro_imovel",
        headers={**HEADERS_SB, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "filtro_id,imovel_id"},
        json=lote,
        timeout=60,
    )
    if not resp.ok:
        print(f"ERRO ao gravar associações filtro-imóvel: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()


def registrar_historico(lote: list[dict]) -> None:
    if not lote:
        return
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/historico",
        headers={**HEADERS_SB, "Prefer": "return=minimal"},
        json=lote,
        timeout=60,
    )
    if not resp.ok:
        print(f"ERRO ao gravar histórico: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()


def _get_json(tabela: str, params: dict) -> list[dict]:
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/{tabela}", headers=HEADERS_SB, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def carregar_perfis() -> dict[str, dict]:
    """user_id -> preferências, só de quem conectou o Telegram."""
    perfis = {}
    for row in _get_json("perfil_usuario", {"telegram_chat_id": "not.is.null", "select": "*"}):
        if not row.get("telegram_chat_id"):
            continue
        perfis[row["user_id"]] = {
            "chat_id": row["telegram_chat_id"],
            "avisar_novo": row.get("avisar_novo", True) is not False,
            "avisar_mudanca": row.get("avisar_mudanca", True) is not False,
            "lembrete_vespera": row.get("lembrete_vespera") or "favoritos",
            "formato": row.get("formato_aviso") or "individual",
            "avisos_prazo": preferencias_prazo(row.get("avisos_prazo")),
        }
    return perfis


def carregar_interesses(user_ids: list[str]) -> dict[str, dict[str, dict]]:
    """user_id -> {imovel_id: linha de interesse_usuario}."""
    if not user_ids:
        return {}
    linhas = _get_json("interesse_usuario", {
        "user_id": f"in.({','.join(user_ids)})",
        "select": "user_id,imovel_id,favorito,ignorado,removido,removido_preco,removido_modalidade",
    })
    out: dict[str, dict[str, dict]] = {}
    for row in linhas:
        out.setdefault(row["user_id"], {})[row["imovel_id"]] = row
    return out


def silenciado(interesse: dict | None, im: dict) -> bool:
    """Sem interesse nunca avisa; removido da lista só volta a avisar se o
    preço ou a modalidade mudaram desde a remoção. Favorito sempre avisa."""
    if not interesse:
        return False
    if interesse.get("favorito"):
        return False
    if interesse.get("ignorado"):
        return True
    if interesse.get("removido"):
        mesmo_preco = round(float(interesse.get("removido_preco") or 0), 2) == round(float(im.get("preco") or 0), 2)
        mesma_mod = (interesse.get("removido_modalidade") or "") == (im.get("modalidade") or "")
        return mesmo_preco and mesma_mod
    return False


def formatar_valor_telegram(v) -> str:
    if v is None:
        return "-"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _esc(txt) -> str:
    return html_module.escape(str(txt or ""), quote=False)


def ler_data_leilao(txt: str | None):
    """'17/10/2026 - 10h00' -> (date, '10h') ou (None, None)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})(?:\D+(\d{1,2})[h:](\d{2}))?", txt or "")
    if not m:
        return None, None
    try:
        d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
    except ValueError:
        return None, None
    hora = None
    if m.group(4):
        hora = f"{int(m.group(4))}h" + ("" if m.group(5) == "00" else m.group(5))
    return d, hora


DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _data_curta(txt: str | None) -> str:
    d, hora = ler_data_leilao(txt)
    if not d:
        return _esc(txt)
    base = f"{DIAS_SEMANA[d.weekday()]} {d.strftime('%d/%m')}"
    return f"{base} às {hora}" if hora else base


def montar_mensagem(im: dict, motivo: str, favorito: bool, imovel_id: str | None,
                    linha_extra: str | None = None, mostrar_pracas: bool = True) -> tuple[str, list[tuple[str, str]]]:
    linhas = []
    if favorito:
        linhas.append("⭐ <b>Seu favorito</b>")
    linhas.append(f"<b>{_esc(motivo)}</b>")
    linhas.append(f"<b>{_esc(im.get('bairro') or 'Bairro não informado')}, {_esc(im.get('cidade'))} ({_esc(im.get('uf'))})</b>")
    endereco = im.get("endereco") or ""
    if im.get("empreendimento"):
        endereco = f"{im['empreendimento']}, {endereco}"
    if endereco:
        linhas.append(_esc(endereco))
    if linha_extra:
        linhas.append(linha_extra)

    hoje = datetime.now(FUSO_BRASILIA).date()
    pracas = []
    for n in (1, 2):
        valor = im.get(f"valor_{n}_leilao")
        if valor is None:
            continue
        d, _ = ler_data_leilao(im.get(f"data_{n}_leilao"))
        texto = f"{n}º leilão, {_data_curta(im.get(f'data_{n}_leilao'))}: {formatar_valor_telegram(valor)}"
        pracas.append(f"<s>{texto}</s>" if d and d < hoje else texto)
    if pracas:
        if mostrar_pracas:
            linhas.extend(pracas)
    else:
        linhas.append(f"{_esc(im.get('modalidade') or 'Venda')}: <b>{formatar_valor_telegram(im.get('preco'))}</b>")
    linhas.append(f"Avaliação: {formatar_valor_telegram(im.get('valor_avaliacao'))}")

    botoes = []
    if imovel_id:
        botoes.append(("Ver no Radar", f"{PAINEL_URL}#imovel={imovel_id}"))
    if im.get("link"):
        botoes.append(("Abrir na Caixa", im["link"]))
    return "\n".join(linhas), botoes


def enviar_telegram(chat_id: str, texto: str, botoes: list[tuple[str, str]] | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    corpo = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True}
    if botoes:
        corpo["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u} for t, u in botoes]]}
    try:
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=corpo, timeout=30)
        if not resp.ok:
            print(f"    aviso: Telegram recusou a mensagem pra {chat_id} ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
        return resp.ok
    except Exception as exc:  # noqa: BLE001
        print(f"    aviso: falha ao enviar Telegram pra {chat_id}: {exc}", file=sys.stderr)
        return False


TOKEN_VINCULO = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def resolver_token_vinculo(token: str) -> str | None:
    """Código de uso único do QR Code -> user_id, se ainda não expirou."""
    if not TOKEN_VINCULO.match(token or ""):
        return None
    linhas = _get_json("perfil_usuario", {
        "vinculo_token": f"eq.{token}",
        "vinculo_expira": f"gt.{datetime.now(timezone.utc).isoformat()}",
        "select": "user_id",
    })
    return linhas[0]["user_id"] if linhas else None


def vincular_chat_id_telegram(user_id: str, chat_id: str) -> bool:
    """Grava direto via service_role (aqui não há sessão de pessoa logada) e apaga
    o código de uso único, que não pode ser usado de novo."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/perfil_usuario",
        headers={**HEADERS_SB, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "user_id"},
        json={"user_id": user_id, "telegram_chat_id": str(chat_id), "vinculo_token": None, "vinculo_expira": None},
        timeout=30,
    )
    if not resp.ok:
        print(f"    aviso: falha ao vincular chat_id do usuário {user_id}: {resp.status_code} {resp.text}", file=sys.stderr)
    return resp.ok


def processar_vinculos_telegram() -> list[str]:
    """Lê as mensagens pendentes do bot (via getUpdates), acha quem mandou
    '/start <user_id>' pelo link/QR Code do painel, e vincula o chat_id
    daquela pessoa à conta dela — sem precisar de servidor rodando o
    tempo todo, só aproveitando as execuções agendadas do robô."""
    falhas_vinculo: list[str] = []
    if not TELEGRAM_BOT_TOKEN:
        return falhas_vinculo
    # Com webhook ligado, a Edge Function do Supabase conecta as contas na hora e
    # o Telegram recusa o getUpdates (conflito). Então, nesse caso, não há o que ler aqui.
    try:
        info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo", timeout=20).json()
        if isinstance(info, dict) and (info.get("result") or {}).get("url"):
            print("Telegram: conexão automática por webhook ativa — não preciso ler mensagens aqui.")
            return falhas_vinculo
    except Exception:  # noqa: BLE001
        pass  # se não deu pra checar, segue pro método antigo
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"timeout": 0},
            timeout=30,
        )
        resp.raise_for_status()
        dados = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui checar mensagens do Telegram: {exc}", file=sys.stderr)
        return falhas_vinculo

    resultados = dados.get("result", [])
    if not resultados:
        return falhas_vinculo

    maior_update_id = None
    vinculados = 0
    ja_vinculados: set = set()
    for atualizacao in resultados:
        maior_update_id = atualizacao["update_id"]
        msg = atualizacao.get("message") or {}
        texto = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id")
        if not chat_id or not texto.startswith("/start"):
            continue
        partes = texto.split(maxsplit=1)
        if len(partes) < 2:
            continue
        codigo = partes[1].strip()
        if (codigo, chat_id) in ja_vinculados:
            continue
        ja_vinculados.add((codigo, chat_id))
        try:
            user_id = resolver_token_vinculo(codigo)
        except Exception as exc:  # noqa: BLE001
            print(f"    aviso: não consegui conferir o código de conexão: {exc}", file=sys.stderr)
            user_id = None
        if user_id is None:
            texto = "⚠️ Esse link expirou ou já foi usado. Abra o painel e gere o QR Code de novo."
        elif vincular_chat_id_telegram(user_id, chat_id):
            vinculados += 1
            texto = "✅ Pronto! A partir de agora você recebe aqui os avisos de imóvel do Radar de Leilões Caixa."
        else:
            texto = ("⚠️ Recebi o seu pedido, mas não consegui salvar a conexão. "
                     "Avise o administrador do sistema; depois escaneie o QR Code de novo.")
            falhas_vinculo.append(f"não consegui salvar a conexão do Telegram do usuário {user_id}")
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": texto},
                timeout=15,
            )
        except Exception:  # noqa: BLE001
            pass

    if vinculados:
        print(f"Telegram: {vinculados} conta(s) vinculada(s) via /start.")

    # confirma o recebimento pro Telegram parar de reenviar essas mensagens
    if maior_update_id is not None:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": maior_update_id + 1, "timeout": 0},
                timeout=30,
            )
        except Exception:  # noqa: BLE001
            pass
    return falhas_vinculo


def executar(args, problemas: list[tuple[str, str]]) -> dict:
    """A busca em si. Problemas que não derrubam a execução vão pra lista
    `problemas` como (chave, texto) e viram aviso pro administrador."""
    for falha in processar_vinculos_telegram():
        problemas.append(("vinculo", falha[0].upper() + falha[1:] + ". Provável falta de permissão na tabela perfil_usuario (rode liberar_acesso.sql)."))

    filtros = carregar_filtros_ativos()
    if not filtros:
        print("Nenhum filtro ativo cadastrado no Supabase. Nada a fazer.")
        return {"novos": 0, "mudados": 0, "ufs": []}

    print(f"{len(filtros)} filtro(s) ativo(s):")
    for f in filtros:
        faixa = f"{formatar_valor(f['valor_min'])} até {formatar_valor(f['valor_max']) if f['valor_max'] != float('inf') else 'sem limite'}"
        bairros_txt = ", ".join(f["bairros"]) if f["bairros"] else "qualquer bairro"
        extra = " — com critérios avançados" if f.get("criterios") else ""
        print(f"  - {f['nome']}: {f['cidade']}/{f['estado']} — {bairros_txt} — {faixa}{extra}")

    ufs = sorted({f["estado"] for f in filtros})
    imoveis_por_uf: dict[str, list[dict]] = {}
    for uf in ufs:
        try:
            texto_csv = download_uf(uf)
            imoveis_por_uf[uf] = parse_properties(texto_csv, uf)
            print(f"[{uf}] {len(imoveis_por_uf[uf])} imóveis no CSV oficial.")
            if not imoveis_por_uf[uf]:
                problemas.append((f"{uf}:vazio", f"{uf}: a lista da Caixa veio vazia (0 imóveis)."))
        except Exception as exc:  # noqa: BLE001
            print(f"[{uf}] ERRO ao baixar/processar: {exc}", file=sys.stderr)
            imoveis_por_uf[uf] = []
            problemas.append((f"{uf}:download", f"{uf}: não consegui baixar ou ler a lista da Caixa ({str(exc)[:160]})."))

    # casa cada imóvel contra cada filtro; um mesmo imóvel pode bater em mais de um
    resultados_por_chave: dict[str, dict] = {}
    chaves_por_filtro: dict[str, list[str]] = {f["id"]: [] for f in filtros}
    for f in filtros:
        for im in imoveis_por_uf.get(f["estado"], []):
            if imovel_bate(im, f["cidade"], f["bairros"], f["valor_min"], f["valor_max"], f.get("criterios")):
                chave = f"{im['uf']}:{im['numero_imovel']}"
                if chave not in resultados_por_chave:
                    resultados_por_chave[chave] = dict(im)
                chaves_por_filtro[f["id"]].append(chave)

    resultados = list(resultados_por_chave.values())
    print(f"{len(resultados)} imóveis únicos bateram em algum filtro.")

    for im in resultados:
        im["fatos"] = parse_descricao_estruturada(im.get("descricao"))

    # compara com o que já está salvo, pra saber o que é novo/mudou e não bater
    # na página de detalhe da Caixa à toa pro que já sabíamos
    estado_anterior = carregar_estado_atual(ufs)
    alvo_detalhe = []
    for im in resultados:
        chave = f"{im['uf']}:{im['numero_imovel']}"
        anterior = estado_anterior.get(chave)
        im["_eh_novo"] = anterior is None
        im["_preco_anterior"] = anterior.get("preco") if anterior else None
        im["_mudou"] = anterior is not None and (
            (anterior.get("modalidade") or "") != (im.get("modalidade") or "")
            or round(float(anterior.get("preco") or 0), 2) != round(float(im.get("preco") or 0), 2)
        )
        if im["_eh_novo"] or im["_mudou"] or args.atualizar_detalhes:
            alvo_detalhe.append(im)
        elif anterior:
            for campo in CAMPOS_DETALHE:
                if im.get(campo) is None:
                    im[campo] = anterior.get(campo)

    if not args.sem_detalhes and alvo_detalhe:
        if args.atualizar_detalhes:
            print(f"Relendo o detalhe de TODOS os {len(alvo_detalhe)} imóveis dos alertas (atualização forçada, sem avisos extras)...")
        else:
            print(f"Buscando detalhe individual de {len(alvo_detalhe)} imóveis novos ou com modalidade/preço "
                  f"diferentes de antes (os que não mudaram reaproveitam o que já tínhamos)...")
        erros_detalhe = 0
        for i, im in enumerate(alvo_detalhe, start=1):
            print(f"  [{i}/{len(alvo_detalhe)}] imóvel nº {im['numero_imovel']}...")
            detalhe = buscar_detalhe(im["numero_imovel"])
            if detalhe.get("_erro_detalhe"):
                erros_detalhe += 1
                print(f"    aviso: não consegui buscar o detalhe ({detalhe['_erro_detalhe']})")
            else:
                im.update({k: v for k, v in detalhe.items() if v is not None})
                print("    empreendimento: " + (im.get("empreendimento") or "(sem nome na página)"))
            # página que falhou ou veio vazia não pode apagar o que já estava salvo
            anterior = estado_anterior.get(f"{im['uf']}:{im['numero_imovel']}") or {}
            for campo in CAMPOS_DETALHE:
                if im.get(campo) is None and anterior.get(campo) is not None:
                    im[campo] = anterior[campo]
            time.sleep(0.6)  # educado com o servidor da Caixa
        if erros_detalhe >= 3 and erros_detalhe > len(alvo_detalhe) * 0.25:
            problemas.append(("detalhe", f"{erros_detalhe} de {len(alvo_detalhe)} páginas de detalhe não abriram. "
                                         "Esses imóveis ficaram sem data de leilão por enquanto."))
    elif not alvo_detalhe:
        print("Nada novo ou alterado desde a última execução — não precisei buscar nenhuma página de detalhe agora.")

    agora = datetime.now(timezone.utc).isoformat()
    lote_upsert = []
    lote_historico = []
    novos, mudados = 0, 0
    for im in resultados:
        registro = {
            "uf": im["uf"],
            "numero_imovel": im["numero_imovel"],
            "numero_formatado": im.get("numero_formatado"),
            "empreendimento": im.get("empreendimento"),
            "cep": im.get("cep"),
            "cidade": im.get("cidade"),
            "bairro": im.get("bairro"),
            "endereco": im.get("endereco"),
            "valor_avaliacao": im.get("valor_avaliacao"),
            "preco": im.get("preco"),
            "desconto": im.get("desconto"),
            "modalidade": im.get("modalidade"),
            "financiamento": im.get("financiamento"),
            "descricao": im.get("descricao"),
            "fatos": im.get("fatos"),
            "link": im.get("link"),
            "foto_url": im.get("foto_url"),
            "matricula": im.get("matricula"),
            "inscricao_imobiliaria": im.get("inscricao_imobiliaria"),
            "comarca": im.get("comarca"),
            "edital": im.get("edital"),
            "leiloeiro": im.get("leiloeiro"),
            "valor_1_leilao": im.get("valor_1_leilao"),
            "data_1_leilao": im.get("data_1_leilao"),
            "valor_2_leilao": im.get("valor_2_leilao"),
            "data_2_leilao": im.get("data_2_leilao"),
            "atualizado_em": agora,
        }
        lote_upsert.append(registro)
        if im["_eh_novo"]:
            novos += 1
        elif im["_mudou"]:
            mudados += 1
            lote_historico.append({
                "uf": im["uf"], "numero_imovel": im["numero_imovel"],
                "modalidade": im.get("modalidade"), "preco": im.get("preco"),
                "detectado_em": agora,
            })

    for i in range(0, len(lote_upsert), 200):
        upsert_imoveis(lote_upsert[i:i + 200])
    registrar_historico(lote_historico)

    # agora que os imóveis estão gravados, pega os ids deles pra montar as
    # associações filtro_imovel — substitui por completo as associações
    # antigas de cada filtro pelas atuais (assim, quem deixou de bater some
    # sozinho, mesmo sem "excluir" nada manualmente)
    ids_por_chave = buscar_ids_imoveis(ufs) if ufs else {}
    total_associacoes = 0
    for f in filtros:
        limpar_associacoes_filtro(f["id"])
        pares = []
        vistos = set()
        for chave in chaves_por_filtro.get(f["id"], []):
            imovel_id = ids_por_chave.get(chave)
            if imovel_id and imovel_id not in vistos:
                vistos.add(imovel_id)
                pares.append({"filtro_id": f["id"], "imovel_id": imovel_id})
        for i in range(0, len(pares), 200):
            inserir_associacoes_filtro(pares[i:i + 200])
        total_associacoes += len(pares)

    # Telegram: avisos por usuário, só do que bate nos filtros DELE, respeitando
    # as preferências e o que ele marcou como sem interesse / removido
    if TELEGRAM_BOT_TOKEN:
        try:
            enviar_avisos_e_lembretes(filtros, chaves_por_filtro, resultados_por_chave, ids_por_chave)
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: falha ao enviar avisos no Telegram: {exc}", file=sys.stderr)
            problemas.append(("telegram", f"Falha ao enviar avisos de imóveis no Telegram ({str(exc)[:160]})."))
    else:
        print("Telegram não configurado (secret TELEGRAM_BOT_TOKEN ausente) — pulei os avisos.")

    print(f"Concluído. {novos} imóvel(is) novo(s), {mudados} com situação alterada, "
          f"{total_associacoes} associação(ões) filtro-imóvel atualizadas.")
    return {"novos": novos, "mudados": mudados, "ufs": ufs}


def _buscar_imoveis_por_id(ids: list[str]) -> dict[str, dict]:
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 80):
        for row in _get_json("imoveis", {"id": f"in.({','.join(ids[i:i + 80])})", "select": "*"}):
            out[row["id"]] = row
    return out


# ------------------------------------------------- avisos de prazo (licitação aberta e venda direta/online)
# O prazo é informado por quem usa o painel (tabela prazo_imovel, compartilhada). Os avisos saem
# nos horários das 3 buscas (10h, 15h e 20h, Brasília): não há aviso no minuto exato.

CHAVES_AVISO_PRAZO = ("lic_vespera", "lic_dia", "vd_3d", "vd_1d", "vd_dia")
BUSCAS_DO_DIA = (10, 15, 20)
DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def preferencias_prazo(valor) -> dict[str, bool]:
    """Tudo ligado, a menos que a pessoa tenha desligado um aviso."""
    prefs = {k: True for k in CHAVES_AVISO_PRAZO}
    if isinstance(valor, dict):
        for k in CHAVES_AVISO_PRAZO:
            if valor.get(k) is False:
                prefs[k] = False
    return prefs


def chave_preferencia(tipo_aviso: str) -> str:
    return "vd_dia" if tipo_aviso.startswith("vd_dia") else tipo_aviso


def avisos_devidos(grupo: str, prazo: datetime, agora: datetime) -> list[str]:
    """Qual aviso de prazo vale NESTA rodada (no máximo um). `grupo` é 'licitacao' ou 'direta'.
    A deduplicação (não mandar duas vezes) fica por conta de quem chama."""
    prazo = prazo.astimezone(FUSO_BRASILIA)
    agora = agora.astimezone(FUSO_BRASILIA)
    if prazo <= agora:
        return []
    dias = (prazo.date() - agora.date()).days
    if grupo == "licitacao":
        if dias == 1 and agora.hour >= 18:      # véspera: só na busca da noite
            return ["lic_vespera"]
        if dias == 0:                           # no dia: na primeira busca que ainda pega o prazo no futuro
            return ["lic_dia"]
        return []
    if grupo == "direta":
        if dias == 3:
            return ["vd_3d"]
        if dias == 1:
            return ["vd_1d"]
        if dias == 0:
            minuto_prazo = prazo.hour * 60 + prazo.minute
            buscas_antes = [h for h in BUSCAS_DO_DIA if h * 60 + 5 < minuto_prazo]
            ultima = max(buscas_antes) if buscas_antes else None
            if ultima is not None and ultima >= 15 and agora.hour >= ultima:
                return ["vd_dia_b"]             # a última busca antes do fim
            return ["vd_dia_a"]                 # a primeira busca do dia
    return []


def _hora_txt(dt: datetime) -> str:
    return f"{dt.hour}h" + (f"{dt.minute:02d}" if dt.minute else "")


def _dia_txt(dt: datetime) -> str:
    return f"{DIAS_SEMANA[dt.weekday()]} {dt:%d/%m}"


def faltam_txt(prazo: datetime, agora: datetime) -> str:
    minutos = int((prazo - agora).total_seconds() // 60)
    if minutos < 60:
        m = max(5, int(round(minutos / 5.0)) * 5)
        return f"Faltam cerca de {m} minutos."
    horas = int(round(minutos / 60.0))
    return "Falta cerca de 1 hora." if horas == 1 else f"Faltam cerca de {horas} horas."


def texto_aviso_prazo(tipo_aviso: str, prazo: datetime, agora: datetime) -> tuple[str, str]:
    """(título, linhas extras) da mensagem."""
    prazo = prazo.astimezone(FUSO_BRASILIA)
    agora = agora.astimezone(FUSO_BRASILIA)
    dia, hora = _dia_txt(prazo), _hora_txt(prazo)
    nota_lic = "<i>Data informada no Radar. Confira no edital.</i>"
    nota_vd = "<i>Prazo informado no Radar. Confira na Caixa.</i>"
    if tipo_aviso == "lic_vespera":
        return "Licitação amanhã", f"Licitação: <b>{dia} às {hora}</b>.\n{nota_lic}"
    if tipo_aviso == "lic_dia":
        return "Licitação hoje", f"A licitação é <b>hoje, às {hora}</b>.\n{nota_lic}"
    if tipo_aviso == "vd_3d":
        return "Prazo final em 3 dias", f"Venda direta até <b>{dia} às {hora}</b>.\n{nota_vd}"
    if tipo_aviso == "vd_1d":
        return "Prazo final amanhã", f"Venda direta encerra <b>amanhã, às {hora}</b>.\n{nota_vd}"
    return "Prazo final hoje", f"Venda direta encerra <b>hoje, às {hora}</b>.\n{faltam_txt(prazo, agora)}\n{nota_vd}"


def _parse_iso(txt) -> datetime:
    return datetime.fromisoformat(str(txt).replace("Z", "+00:00"))


def enviar_avisos_de_prazo(perfis: dict, interesses: dict, por_usuario: dict, agora_local: datetime) -> None:
    """Quem recebe: quem tem o imóvel nos favoritos ou nos alertas ativos (e não marcou 'sem interesse').
    Vale para favoritos e não favoritos. Não passa pelo resumo das 20h: aviso de prazo é na hora."""
    agora = agora_local
    try:
        linhas = _get_json("prazo_imovel", {
            "select": "imovel_id,data_hora",
            "data_hora": f"gt.{(agora - timedelta(hours=1)).astimezone(timezone.utc).isoformat()}",
        })
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui ler os prazos informados ({exc})", file=sys.stderr)
        return
    prazos: dict[str, datetime] = {}
    for r in linhas:
        try:
            prazo = _parse_iso(r["data_hora"]).astimezone(FUSO_BRASILIA)
        except Exception:  # noqa: BLE001
            continue
        if timedelta(0) < prazo - agora <= timedelta(days=5):
            prazos[r["imovel_id"]] = prazo
    if not prazos:
        print("Telegram: 0 aviso(s) de prazo enviado(s).")
        return

    candidatos: list[tuple[str, str]] = []
    for user_id in perfis:
        inter = interesses.get(user_id, {})
        ids = {i for i, r in inter.items() if r.get("favorito")} | set(por_usuario.get(user_id, {}).keys())
        candidatos += [(user_id, i) for i in sorted(ids & prazos.keys())]
    if not candidatos:
        print("Telegram: 0 aviso(s) de prazo enviado(s).")
        return

    imoveis = _buscar_imoveis_por_id(sorted(prazos))
    try:
        enviados = {(r["user_id"], r["imovel_id"], r["chave"]) for r in _get_json("aviso_prazo_enviado", {
            "imovel_id": f"in.({','.join(sorted(prazos))})", "select": "user_id,imovel_id,chave"})}
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui ler os avisos de prazo já enviados ({exc}); não envio nada para não repetir.", file=sys.stderr)
        return

    total = 0
    registros: list[dict] = []
    for user_id, imovel_id in candidatos:
        im = imoveis.get(imovel_id)
        if not im:
            continue
        grupo = grupo_modalidade(im.get("modalidade"))
        if grupo not in ("licitacao", "direta"):
            continue
        inter = interesses.get(user_id, {})
        if silenciado(inter.get(imovel_id), im):
            continue
        prazo, perfil = prazos[imovel_id], perfis[user_id]
        sufixo = prazo.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
        for tipo in avisos_devidos(grupo, prazo, agora):
            if not perfil["avisos_prazo"].get(chave_preferencia(tipo), True):
                continue
            chave = f"{tipo}:{sufixo}"
            if (user_id, imovel_id, chave) in enviados:
                continue
            fav = bool(inter.get(imovel_id, {}).get("favorito"))
            titulo, extra = texto_aviso_prazo(tipo, prazo, agora)
            texto, botoes = montar_mensagem(im, titulo, fav, imovel_id, extra, mostrar_pracas=False)
            if enviar_telegram(perfil["chat_id"], texto, botoes):
                total += 1
                registros.append({"user_id": user_id, "imovel_id": imovel_id, "chave": chave})
                if tipo == "vd_dia_b":   # se a fase da manhã não saiu, esta mensagem cobre as duas
                    registros.append({"user_id": user_id, "imovel_id": imovel_id, "chave": f"vd_dia_a:{sufixo}"})
            time.sleep(1)
    if registros:
        resp = requests.post(f"{SUPABASE_URL}/rest/v1/aviso_prazo_enviado",
                             headers={**HEADERS_SB, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                             json=registros, timeout=30)
        if not resp.ok:
            print(f"aviso: não consegui registrar os avisos de prazo enviados: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
    print(f"Telegram: {total} aviso(s) de prazo enviado(s).")


def enfileirar_avisos(linhas: list[dict]) -> bool:
    resp = requests.post(f"{SUPABASE_URL}/rest/v1/aviso_pendente",
                         headers={**HEADERS_SB, "Prefer": "return=minimal"}, json=linhas, timeout=30)
    if not resp.ok:
        print(f"aviso: não consegui guardar avisos pro resumo: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
    return resp.ok


def montar_resumo(itens: list[dict], imoveis: dict[str, dict], inter: dict) -> list[str]:
    """Uma mensagem (ou mais, se passar de ~3.500 caracteres) com tudo do dia.
    Favoritos num bloco próprio, no topo."""
    por_imovel: dict[str, dict] = {}
    for it in itens:
        if it["imovel_id"] not in por_imovel or it["tipo"] == "novo":  # "novo" vale mais que "mudou"
            por_imovel[it["imovel_id"]] = it
    favoritos, novos, mudancas = [], [], []
    for imovel_id, it in por_imovel.items():
        im = imoveis.get(imovel_id)
        if not im or silenciado(inter.get(imovel_id), im):
            continue
        fav = bool(inter.get(imovel_id, {}).get("favorito"))
        valor = formatar_valor_telegram(im.get("preco"))
        if it["tipo"] == "mudou" and it.get("detalhe"):
            valor = f"de <s>{_esc(it['detalhe'])}</s> para <b>{valor}</b>"
        else:
            valor = f"<b>{valor}</b>"
        rotulo = "Novo" if it["tipo"] == "novo" else "Mudou"
        linha = (f"• <b>{_esc(im.get('bairro') or 'Bairro não informado')}</b>, {_esc(im.get('cidade'))} ({_esc(im.get('uf'))}). "
                 f"{valor}, {_esc(im.get('modalidade') or 'venda')}. "
                 f"<a href=\"{PAINEL_URL}#imovel={imovel_id}\">Ver no Radar</a>")
        if fav:
            favoritos.append(f"{linha} ({rotulo.lower()})")
        elif it["tipo"] == "novo":
            novos.append(linha)
        else:
            mudancas.append(linha)
    n_novos = sum(1 for it in por_imovel.values() if it["tipo"] == "novo" and it["imovel_id"] in imoveis)
    n_mud = sum(1 for it in por_imovel.values() if it["tipo"] == "mudou" and it["imovel_id"] in imoveis)
    if not (favoritos or novos or mudancas):
        return []
    cabecalho = ["📋 <b>Resumo do dia</b>",
                 f"{n_novos} {'novo' if n_novos == 1 else 'novos'}, {n_mud} {'mudança' if n_mud == 1 else 'mudanças'} de praça ou de preço.", ""]
    blocos: list[str] = []
    if favoritos:
        blocos += ["⭐ <b>Seus favoritos</b>", *favoritos, ""]
    if novos:
        blocos += ["<b>Imóveis novos</b>", *novos, ""]
    if mudancas:
        blocos += ["<b>Mudanças de praça ou de preço</b>", *mudancas, ""]
    mensagens, atual = [], list(cabecalho)
    for linha in blocos:
        if len("\n".join(atual)) + len(linha) > 3500:
            mensagens.append("\n".join(atual).strip())
            atual = ["<i>(continuação do resumo)</i>", ""]
        atual.append(linha)
    mensagens.append("\n".join(atual).strip())
    return mensagens


def enviar_resumos(perfis: dict, interesses: dict, agora_local: datetime) -> None:
    quem = [u for u, p in perfis.items() if p.get("formato") == "resumo"]
    if not quem:
        return
    try:
        linhas = _get_json("aviso_pendente", {"user_id": f"in.({','.join(quem)})", "select": "*", "order": "criado_em.asc"})
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui ler a fila do resumo: {exc}", file=sys.stderr)
        return
    por_user: dict[str, list[dict]] = {}
    for l in linhas:
        por_user.setdefault(l["user_id"], []).append(l)
    enviados = 0
    for user_id, itens in por_user.items():
        mais_antigo = min(datetime.fromisoformat(l["criado_em"].replace("Z", "+00:00")) for l in itens)
        horas = (datetime.now(timezone.utc) - mais_antigo).total_seconds() / 3600
        if agora_local.hour < 18 and horas < 20:
            continue  # ainda não é a busca da noite
        imoveis = _buscar_imoveis_por_id(sorted({l["imovel_id"] for l in itens}))
        mensagens = montar_resumo(itens, imoveis, interesses.get(user_id, {}))
        chat_id = perfis[user_id]["chat_id"]
        if all(enviar_telegram(chat_id, m) for m in mensagens):
            enviados += 1 if mensagens else 0
            ids = [l["id"] for l in itens]
            for i in range(0, len(ids), 80):
                requests.delete(f"{SUPABASE_URL}/rest/v1/aviso_pendente", headers=HEADERS_SB,
                                params={"id": f"in.({','.join(ids[i:i + 80])})"}, timeout=30)
        time.sleep(1)
    print(f"Telegram: {enviados} resumo(s) enviado(s).")


def enviar_avisos_e_lembretes(filtros, chaves_por_filtro, resultados_por_chave, ids_por_chave) -> None:
    perfis = carregar_perfis()
    if not perfis:
        print("Telegram: ninguém conectado ainda — nenhum aviso a enviar.")
        return
    interesses = carregar_interesses(list(perfis.keys()))

    # quais imóveis (id) cada usuário tem pelos filtros ATIVOS, e por qual filtro
    por_usuario: dict[str, dict[str, dict]] = {}
    for f in filtros:
        if f["user_id"] not in perfis:
            continue
        for chave in chaves_por_filtro.get(f["id"], []):
            imovel_id = ids_por_chave.get(chave)
            im = resultados_por_chave.get(chave)
            if not imovel_id or not im:
                continue
            por_usuario.setdefault(f["user_id"], {})[imovel_id] = im

    agora_local = datetime.now(FUSO_BRASILIA)

    # 1) imóvel novo / mudança de praça ou preço
    total = 0
    enfileirados = 0
    for user_id, itens in por_usuario.items():
        perfil = perfis[user_id]
        inter = interesses.get(user_id, {})
        fila = []
        para_resumo = []   # (linha da fila, fav, texto, botoes) de quem prefere o resumo das 20h
        for imovel_id, im in itens.items():
            if silenciado(inter.get(imovel_id), im):
                continue
            fav = bool(inter.get(imovel_id, {}).get("favorito"))
            tipo = detalhe = None
            if im.get("_eh_novo") and perfil["avisar_novo"]:
                tipo, motivo, extra = "novo", "Imóvel novo", None
            elif im.get("_mudou") and perfil["avisar_mudanca"]:
                tipo, motivo, extra = "mudou", "Mudou de praça ou de preço", None
                if im.get("_preco_anterior") is not None and im.get("preco") is not None:
                    detalhe = formatar_valor_telegram(im["_preco_anterior"])
                    extra = (f"De <s>{detalhe}</s> para <b>{formatar_valor_telegram(im['preco'])}</b>.")
            if not tipo:
                continue
            texto_botoes = montar_mensagem(im, motivo, fav, imovel_id, extra)
            if perfil.get("formato") == "resumo":
                para_resumo.append(({"user_id": user_id, "imovel_id": imovel_id, "tipo": tipo, "detalhe": detalhe}, fav, *texto_botoes))
            else:
                fila.append((fav, texto_botoes))
        if para_resumo:
            if enfileirar_avisos([linha for linha, *_ in para_resumo]):
                enfileirados += len(para_resumo)
            else:  # não conseguiu guardar na fila: manda na hora, pra ninguém ficar sem aviso
                fila.extend((fav, (texto, botoes)) for _, fav, texto, botoes in para_resumo)
        fila.sort(key=lambda x: not x[0])  # favoritos primeiro
        for _, (texto, botoes) in fila:
            if enviar_telegram(perfil["chat_id"], texto, botoes):
                total += 1
            time.sleep(1)
    print(f"Telegram: {total} aviso(s) de imóvel enviado(s), {enfileirados} guardado(s) pro resumo da noite.")

    # 1b) resumo único — vai na busca da noite (ou se algo ficou parado por mais de 20 h)
    enviar_resumos(perfis, interesses, agora_local)

    # 1c) prazos informados (licitação aberta e venda direta/online): sempre na hora, nunca no resumo
    enviar_avisos_de_prazo(perfis, interesses, por_usuario, agora_local)

    # 2) lembrete na véspera — só na busca da noite (18h em diante, horário de Brasília)
    if agora_local.hour < 18:
        return
    amanha = agora_local.date() + timedelta(days=1)
    amanha_txt = amanha.strftime("%d/%m/%Y")

    candidatos: dict[str, set[str]] = {}
    for user_id, perfil in perfis.items():
        modo = perfil["lembrete_vespera"]
        if modo == "nao":
            continue
        inter = interesses.get(user_id, {})
        ids = {i for i, r in inter.items() if r.get("favorito")}
        if modo == "todos":
            ids |= set(por_usuario.get(user_id, {}).keys())
        if ids:
            candidatos[user_id] = ids
    if not candidatos:
        return
    todos_ids = set().union(*candidatos.values())
    imoveis = _buscar_imoveis_por_id(sorted(todos_ids))
    ja_enviados = {(r["user_id"], r["imovel_id"]) for r in _get_json("lembrete_enviado", {
        "data_leilao": f"eq.{amanha_txt}", "select": "user_id,imovel_id"})}

    lembretes = 0
    registros = []
    for user_id, ids in candidatos.items():
        perfil = perfis[user_id]
        inter = interesses.get(user_id, {})
        for imovel_id in ids:
            im = imoveis.get(imovel_id)
            if not im or (user_id, imovel_id) in ja_enviados or silenciado(inter.get(imovel_id), im):
                continue
            for n in (1, 2):
                d, _ = ler_data_leilao(im.get(f"data_{n}_leilao"))
                if d != amanha or im.get(f"valor_{n}_leilao") is None:
                    continue
                fav = bool(inter.get(imovel_id, {}).get("favorito"))
                extra = (f"{n}º leilão amanhã, {_data_curta(im.get(f'data_{n}_leilao'))}. "
                         f"Lance mínimo: <b>{formatar_valor_telegram(im[f'valor_{n}_leilao'])}</b>")
                if n == 1 and im.get("valor_2_leilao") is not None:
                    extra += (f"\n<i>Se não vender: {formatar_valor_telegram(im['valor_2_leilao'])} no 2º leilão, "
                              f"{_data_curta(im.get('data_2_leilao'))}.</i>")
                texto, botoes = montar_mensagem(im, "Leilão amanhã", fav, imovel_id, extra, mostrar_pracas=False)
                if enviar_telegram(perfil["chat_id"], texto, botoes):
                    lembretes += 1
                    registros.append({"user_id": user_id, "imovel_id": imovel_id, "data_leilao": amanha_txt})
                time.sleep(1)
                break
    if registros:
        resp = requests.post(f"{SUPABASE_URL}/rest/v1/lembrete_enviado",
                             headers={**HEADERS_SB, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                             json=registros, timeout=30)
        if not resp.ok:
            print(f"aviso: não consegui registrar os lembretes enviados: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
    print(f"Telegram: {lembretes} lembrete(s) de véspera enviado(s).")


# ------------------------------------------------- situação da execução

def _rotulo_busca() -> str:
    return f"Busca das {datetime.now(FUSO_BRASILIA).hour}h"


def _link_execucao() -> str | None:
    srv, repo, run = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    return f"{srv}/{repo}/actions/runs/{run}" if srv and repo and run else None


def carregar_ultimo_status() -> dict | None:
    try:
        linhas = _get_json("status_execucao", {"select": "*", "order": "executado_em.desc", "limit": "1"})
        return linhas[0] if linhas else None
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui ler a última situação ({exc})", file=sys.stderr)
        return None


def registrar_status(status: str, assinatura: str | None, detalhes: str | None) -> None:
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/status_execucao",
                      headers={**HEADERS_SB, "Prefer": "return=minimal"},
                      json={"status": status, "assinatura": assinatura, "detalhes": detalhes}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não consegui registrar a situação ({exc})", file=sys.stderr)


def avisar_admin(texto: str) -> None:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID):
        return
    link = _link_execucao()
    enviar_telegram(TELEGRAM_ADMIN_CHAT_ID, texto, [("Ver detalhes da execução", link)] if link else None)
    # marca pro passo de falha do workflow não mandar um segundo aviso
    try:
        open(os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "radar_alerta_enviado"), "w").close()
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar de Leilões Caixa — roda no GitHub Actions, grava no Supabase.")
    parser.add_argument("--sem-detalhes", action="store_true",
                         help="pula a busca da página individual de cada imóvel (mais rápido, menos dados)")
    parser.add_argument("--atualizar-detalhes", action="store_true",
                         help="relê a página de detalhe de TODOS os imóveis dos alertas, sem gerar avisos "
                              "(útil uma vez, depois de melhorar a leitura da página)")
    args = parser.parse_args()

    ultimo = carregar_ultimo_status()
    problemas: list[tuple[str, str]] = []
    try:
        resumo = executar(args, problemas)
    except Exception as exc:  # noqa: BLE001
        assinatura = f"falha:{type(exc).__name__}"
        detalhes = f"{type(exc).__name__}: {str(exc)[:400]}"
        registrar_status("falha", assinatura, detalhes)
        repetido = ultimo and ultimo.get("status") == "falha" and ultimo.get("assinatura") == assinatura
        if not repetido:
            avisar_admin(f"❌ <b>Busca falhou</b>\n<b>{_rotulo_busca()} não terminou</b>\n{_esc(detalhes)}\n"
                         "Nenhum imóvel foi atualizado nesta rodada. Tento de novo no próximo horário.")
        else:
            # mesmo problema da última vez: não repete o aviso, mas marca pro
            # passo de falha do workflow também não mandar
            try:
                open(os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "radar_alerta_enviado"), "w").close()
            except OSError:
                pass
        raise

    if problemas:
        assinatura = ";".join(sorted(k for k, _ in problemas))
        registrar_status("parcial", assinatura, "\n".join(t for _, t in problemas))
        repetido = ultimo and ultimo.get("status") == "parcial" and ultimo.get("assinatura") == assinatura
        if not repetido:
            corpo = "\n".join(_esc(t) for _, t in problemas)
            avisar_admin(f"⚠️ <b>Busca incompleta</b>\n<b>{_rotulo_busca()} terminou com problemas</b>\n{corpo}")
    else:
        registrar_status("ok", None, None)
        if ultimo and ultimo.get("status") in ("parcial", "falha"):
            ufs = ", ".join(resumo.get("ufs") or []) or "nenhum estado"
            avisar_admin(f"✅ <b>Voltou ao normal</b>\n<b>{_rotulo_busca()} funcionou</b>\n"
                         f"Atualizados: {ufs}. {resumo.get('novos', 0)} imóvel(is) novo(s), "
                         f"{resumo.get('mudados', 0)} com mudança.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
