#!/usr/bin/env python3
"""
Radar de Leilões Caixa — roda no GitHub Actions
--------------------------------------------------
Baixa o CSV oficial da Caixa, aplica os filtros ativos cadastrados no
Supabase (tabela `filtros`), busca a página de detalhe só de quem é novo ou
mudou de situação, e grava tudo no Supabase (tabelas `imoveis` e
`historico`). O painel web (painel/index.html) lê esses dados direto do
Supabase — este script não gera nenhuma página.

Ainda não manda nada pro Telegram — isso entra numa etapa seguinte.

Variáveis de ambiente esperadas (Secrets do GitHub Actions):
    SUPABASE_URL           ex: https://xxxxx.supabase.co
    SUPABASE_SERVICE_KEY   chave service_role (nunca a anon — precisa
                            ignorar RLS pra gravar em `imoveis`/`historico`)

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
from datetime import datetime, timezone

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


def imovel_bate(imovel: dict, cidade: str, bairros: list[str], valor_min: float, valor_max: float) -> bool:
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
    return True


def formatar_valor(v) -> str:
    if v is None:
        return "-"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
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
            "nome": f["nome"],
            "estado": f["estado"].upper(),
            "cidade": f["cidade"],
            "bairros": f.get("bairros") or [],
            "valor_min": f.get("valor_min") or 0,
            "valor_max": f["valor_max"] if f.get("valor_max") is not None else float("inf"),
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


def main() -> int:
    print(f"[debug] URL tem {len(SUPABASE_URL)} caracteres, termina em: {SUPABASE_URL[-30:]!r}")
    parser = argparse.ArgumentParser(description="Radar de Leilões Caixa — roda no GitHub Actions, grava no Supabase.")
    parser.add_argument("--sem-detalhes", action="store_true",
                         help="pula a busca da página individual de cada imóvel (mais rápido, menos dados)")
    args = parser.parse_args()

    filtros = carregar_filtros_ativos()
    if not filtros:
        print("Nenhum filtro ativo cadastrado no Supabase. Nada a fazer.")
        return 0

    print(f"{len(filtros)} filtro(s) ativo(s):")
    for f in filtros:
        faixa = f"{formatar_valor(f['valor_min'])} até {formatar_valor(f['valor_max']) if f['valor_max'] != float('inf') else 'sem limite'}"
        bairros_txt = ", ".join(f["bairros"]) if f["bairros"] else "qualquer bairro"
        print(f"  - {f['nome']}: {f['cidade']}/{f['estado']} — {bairros_txt} — {faixa}")

    ufs = sorted({f["estado"] for f in filtros})
    imoveis_por_uf: dict[str, list[dict]] = {}
    for uf in ufs:
        try:
            texto_csv = download_uf(uf)
            imoveis_por_uf[uf] = parse_properties(texto_csv, uf)
            print(f"[{uf}] {len(imoveis_por_uf[uf])} imóveis no CSV oficial.")
        except Exception as exc:  # noqa: BLE001
            print(f"[{uf}] ERRO ao baixar/processar: {exc}", file=sys.stderr)
            imoveis_por_uf[uf] = []

    # casa cada imóvel contra cada filtro; um mesmo imóvel pode bater em mais de um
    resultados_por_chave: dict[str, dict] = {}
    for f in filtros:
        for im in imoveis_por_uf.get(f["estado"], []):
            if imovel_bate(im, f["cidade"], f["bairros"], f["valor_min"], f["valor_max"]):
                chave = f"{im['uf']}:{im['numero_imovel']}"
                if chave not in resultados_por_chave:
                    copia = dict(im)
                    copia["filtros_correspondentes"] = []
                    resultados_por_chave[chave] = copia
                if f["nome"] not in resultados_por_chave[chave]["filtros_correspondentes"]:
                    resultados_por_chave[chave]["filtros_correspondentes"].append(f["nome"])

    resultados = list(resultados_por_chave.values())
    print(f"{len(resultados)} imóveis únicos bateram em algum filtro.")
    if not resultados:
        return 0

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
        im["_mudou"] = anterior is not None and (
            (anterior.get("modalidade") or "") != (im.get("modalidade") or "")
            or round(float(anterior.get("preco") or 0), 2) != round(float(im.get("preco") or 0), 2)
        )
        if im["_eh_novo"] or im["_mudou"]:
            alvo_detalhe.append(im)
        elif anterior:
            for campo in ("valor_1_leilao", "data_1_leilao", "valor_2_leilao", "data_2_leilao",
                          "matricula", "inscricao_imobiliaria", "comarca", "edital", "leiloeiro",
                          "foto_url", "numero_formatado"):
                if im.get(campo) is None:
                    im[campo] = anterior.get(campo)

    if not args.sem_detalhes and alvo_detalhe:
        print(f"Buscando detalhe individual de {len(alvo_detalhe)} imóveis novos ou com modalidade/preço "
              f"diferentes de antes (os que não mudaram reaproveitam o que já tínhamos)...")
        for i, im in enumerate(alvo_detalhe, start=1):
            print(f"  [{i}/{len(alvo_detalhe)}] imóvel nº {im['numero_imovel']}...")
            detalhe = buscar_detalhe(im["numero_imovel"])
            if detalhe.get("_erro_detalhe"):
                print(f"    aviso: não consegui buscar o detalhe ({detalhe['_erro_detalhe']})")
            else:
                im.update(detalhe)
            time.sleep(0.6)  # educado com o servidor da Caixa
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
            "cidade": im.get("cidade"),
            "bairro": im.get("bairro"),
            "endereco": im.get("endereco"),
            "valor_avaliacao": im.get("valor_avaliacao"),
            "preco": im.get("preco"),
            "desconto": im.get("desconto"),
            "modalidade": im.get("modalidade"),
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
            "filtros_correspondentes": im["filtros_correspondentes"],
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

    print(f"Concluído. {novos} imóvel(is) novo(s), {mudados} com situação alterada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
