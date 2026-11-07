"""Leitura e análise de relatórios (.xlsx / .csv) para importação.

Princípios (ver docs/IMPORTACAO.md):
- Nada é gravado na análise; a gravação só acontece na confirmação, numa única transação.
- O significado das colunas vem de um MAPEAMENTO confirmado pelo usuário (com sugestão automática).
- `Remessa::Tipo Rem.` NÃO é tratado como fábrica automaticamente; só se o usuário escolher.
- Linhas de total/subtotal (fórmulas ou rótulos TOTAL/SALDO/GERAL/DIAS) são ignoradas e contadas.
- Chave estável da leitura (chave_origem): reimportar o mesmo evento não duplica.
  * com identificador de movimento: fábrica|tipo|remessa|OP|etapa|id
  * sem identificador: fábrica|tipo|remessa|OP|etapa|data (a quantidade NÃO entra na chave:
    mesma chave com outra quantidade = alteração detectada, enviada para conferência).
- Linhas idênticas no mesmo arquivo contam uma vez (duplicidade informada). Mesma chave com
  quantidades diferentes no mesmo arquivo = ambígua: não importada, a menos que o usuário
  declare que são movimentações distintas.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from .fluxo import catalogo, normalizar

FORMATOS = {".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv", ".txt": "csv"}
MAX_LINHAS = 200_000
ROTULOS_TOTAL = ("total", "subtotal", "saldo", "geral", "dias", "soma")

CAMPOS = ("fabrica", "tipo_remessa", "remessa", "op", "pedido", "material", "quantidade_total",
          "id_movimento", "etapa", "data", "quantidade")

ALIASES = {
    "fabrica": ["fabrica", "fab", "codfabrica", "codigofabrica", "unidade", "fabricacodigo"],
    "tipo_remessa": ["remessatiporem", "tiporem", "tiporemessa", "remessatipo"],
    "remessa": ["remessanr", "remessa", "remessas", "nrremessa", "numeroremessa", "remessanumero"],
    "op": ["remessaop", "op", "ordemproducao", "nrop", "numeroop", "ordem"],
    "pedido": ["pedido", "nrpedido", "numeropedido"],
    "material": ["composicaodescricao", "material", "composicao", "descricaomaterial", "modelo", "produto"],
    "quantidade_total": ["qtdtotal", "quantidadetotal", "total", "qtdprevista", "quantidadeprevista"],
    "id_movimento": ["idmovimento", "idleitura", "movimento", "codleitura", "idmovimentacao", "leituraid"],
    "etapa": ["secao", "setor", "etapa", "secaoprodutiva"],
    "data": ["data", "dataleitura", "datahora", "datamovimento", "dtleitura"],
    "quantidade": ["quantidade", "qtd", "qtdlida", "quantidadelida", "pares"],
}
TOKENS_DATA = ("data", "dt", "datahora", "dia")
TOKENS_QTD = ("qtd", "quant", "quantidade", "pares", "qtde")


class ErroArquivo(ValueError):
    pass


# ======================================================================================
# Leitura do arquivo
# ======================================================================================
def ler_arquivo(nome: str, conteudo: bytes) -> dict:
    if not conteudo:
        raise ErroArquivo("O arquivo está vazio.")
    extensao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
    formato = FORMATOS.get(extensao)
    if formato is None:
        raise ErroArquivo("Formato não suportado. Envie .xlsx ou .csv (arquivos .xls antigos: salve como .xlsx).")
    abas = _ler_xlsx(conteudo) if formato == "xlsx" else _ler_csv(conteudo)
    if not any(a["linhas"] for a in abas):
        raise ErroArquivo("O arquivo não contém linhas com dados.")
    return {"formato": formato, "abas": abas, "sha256": hashlib.sha256(conteudo).hexdigest()}


def _ler_xlsx(conteudo: bytes) -> list[dict]:
    import warnings

    from openpyxl import load_workbook

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb_v = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
            wb_f = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=False)
    except Exception as erro:  # arquivo corrompido ou não é um xlsx de verdade
        raise ErroArquivo(f"Não foi possível abrir a planilha ({type(erro).__name__}).") from None
    abas = []
    for nome in wb_v.sheetnames:
        linhas, formulas = [], set()
        for i, (lv, lf) in enumerate(zip(wb_v[nome].iter_rows(values_only=True),
                                         wb_f[nome].iter_rows(values_only=True))):
            if i >= MAX_LINHAS:
                raise ErroArquivo(f"A aba {nome!r} tem mais de {MAX_LINHAS} linhas.")
            linha = list(lv)
            while linha and linha[-1] in (None, ""):
                linha.pop()
            linhas.append(linha)
            if any(isinstance(f, str) and f.startswith("=") for f in lf):
                formulas.add(i)
        while linhas and not linhas[-1]:
            linhas.pop()
        abas.append({"nome": nome, "linhas": linhas, "formulas": formulas})
    wb_v.close()
    wb_f.close()
    return abas


def _ler_csv(conteudo: bytes) -> list[dict]:
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ErroArquivo("Não foi possível identificar a codificação do CSV.")
    amostra = texto[:5000]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=";,\t|")
        delimitador = dialeto.delimiter
    except csv.Error:
        delimitador = ";" if amostra.count(";") >= amostra.count(",") else ","
    linhas = []
    for i, linha in enumerate(csv.reader(io.StringIO(texto), delimiter=delimitador)):
        if i >= MAX_LINHAS:
            raise ErroArquivo(f"O CSV tem mais de {MAX_LINHAS} linhas.")
        linhas.append([c.strip() if c.strip() != "" else None for c in linha])
    return [{"nome": "CSV", "linhas": linhas, "formulas": set()}]


# ======================================================================================
# Conversões
# ======================================================================================
def texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor).strip()


def inteiro(valor) -> int | None:
    """None se vazio; ValueError se inválido."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, bool):
        raise ValueError
    if isinstance(valor, (int, float)):
        if float(valor) != int(valor):
            raise ValueError
        return int(valor)
    t = str(valor).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):  # 1.200 (milhar pt-BR)
        t = t.replace(".", "")
    t = t.replace(",", ".")
    numero = float(t)
    if not numero.is_integer():
        raise ValueError
    return int(numero)


def data_iso(valor) -> str | None:
    """Data (AAAA-MM-DD) ou data-hora (AAAA-MM-DDTHH:MM[:SS]). None se vazio; ValueError se inválida."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat() if (valor.hour, valor.minute, valor.second) == (0, 0, 0) \
            else valor.replace(microsecond=0).isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (int, float)) and 20000 < float(valor) < 80000:  # número de série do Excel
        return (datetime(1899, 12, 30) + timedelta(days=float(valor))).date().isoformat()
    t = str(valor).strip()
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(t, formato)
        except ValueError:
            continue
        return dt.date().isoformat() if "%H" not in formato else dt.isoformat()
    raise ValueError


def _parece_data(valor) -> bool:
    try:
        return data_iso(valor) is not None and not isinstance(valor, (int, float))
    except ValueError:
        return False


def _parece_numero(valor) -> bool:
    try:
        return inteiro(valor) is not None
    except (ValueError, TypeError):
        return False


# ======================================================================================
# Sugestão de mapeamento
# ======================================================================================
def detectar_cabecalho(linhas: list[list]) -> int:
    melhor, pontos_melhor = 0, -1
    todos = {a for lista in ALIASES.values() for a in lista}
    for i, linha in enumerate(linhas[:40]):
        textos = [normalizar(c) for c in linha if isinstance(c, str) and c.strip()]
        pontos = len(textos) + 3 * sum(1 for t in textos if t in todos)
        if len(textos) >= 2 and pontos > pontos_melhor:
            melhor, pontos_melhor = i, pontos
    return melhor


def _casar_etapa(cabecalho_norm: str, etapas: list[dict]) -> tuple[dict | None, str | None]:
    """Retorna (etapa, tipo) onde tipo é 'data', 'quantidade' ou None (a inferir pelos valores)."""
    for e in etapas:
        nomes = sorted({normalizar(e["nome"]), normalizar(e["codigo"]), *[normalizar(s) for s in e["sinonimos"]]}
                       - {""}, key=len, reverse=True)
        for n in nomes:
            if cabecalho_norm == n:
                return e, None
            for tok in TOKENS_DATA:
                if cabecalho_norm in (n + tok, tok + n):
                    return e, "data"
            for tok in TOKENS_QTD:
                if cabecalho_norm in (n + tok, tok + n):
                    return e, "quantidade"
    return None, None


def sugerir_mapeamento(linhas: list[list], linha_cabecalho: int, etapas: list[dict]) -> dict:
    cabecalho = linhas[linha_cabecalho] if linha_cabecalho < len(linhas) else []
    normalizados = [normalizar(c) if c is not None else "" for c in cabecalho]
    colunas: dict[str, int | None] = {c: None for c in CAMPOS}
    usadas: set[int] = set()
    for campo in ("tipo_remessa", "remessa", "op", "pedido", "material", "quantidade_total", "id_movimento",
                  "fabrica", "etapa", "data"):
        for alias in ALIASES[campo]:
            idx = next((i for i, n in enumerate(normalizados) if n == alias and i not in usadas), None)
            if idx is not None:
                colunas[campo] = idx
                usadas.add(idx)
                break

    amostra = linhas[linha_cabecalho + 1: linha_cabecalho + 60]
    etapas_map: dict[int, dict] = {}
    for i, n in enumerate(normalizados):
        if i in usadas or not n:
            continue
        etapa, tipo = _casar_etapa(n, etapas)
        if etapa is None:
            continue
        if tipo is None:
            valores = [l[i] for l in amostra if i < len(l) and l[i] not in (None, "")]
            tipo = "data" if valores and sum(_parece_data(v) for v in valores) > len(valores) / 2 else "quantidade"
        item = etapas_map.setdefault(etapa["id"], {"etapa_id": etapa["id"], "coluna_data": None,
                                                    "coluna_quantidade": None})
        chave = "coluna_data" if tipo == "data" else "coluna_quantidade"
        if item[chave] is None:
            item[chave] = i
            usadas.add(i)

    longo = colunas["etapa"] is not None
    if longo:
        idx_q = next((i for i, n in enumerate(normalizados) if n in ALIASES["quantidade"] and i not in usadas), None)
        colunas["quantidade"] = idx_q
    else:
        colunas["data"] = None if colunas["data"] is not None and etapas_map else colunas["data"]
    tem_datas = any(e["coluna_data"] is not None for e in etapas_map.values())
    return {
        "linha_cabecalho": linha_cabecalho,
        "layout": "longo" if longo else "largo",
        "colunas": colunas,
        "etapas": list(etapas_map.values()),
        # Sem datas por seção, os números por seção são uma posição (acumulado) na data de referência.
        "tipo_valor": "movimento" if (longo or tem_datas) else "acumulado",
        "data_referencia": None if (longo or tem_datas) else date.today().isoformat(),
        "fabrica_padrao_id": None,
        "ambiguas": "revisar",
    }


# ======================================================================================
# Análise
# ======================================================================================
def _valor(linha: list, idx: int | None):
    if idx is None or idx < 0 or idx >= len(linha):
        return None
    v = linha[idx]
    return None if isinstance(v, str) and not v.strip() else v


def _eh_total(linha: list, eh_formula: bool, col_remessa: int | None) -> bool:
    if eh_formula:
        return True
    remessa = texto(_valor(linha, col_remessa))
    rotulos = [normalizar(c) for c in linha if isinstance(c, str)]
    return (not remessa or not _parece_numero(remessa) and normalizar(remessa).startswith(ROTULOS_TOTAL)) and any(
        r.startswith(ROTULOS_TOTAL) for r in rotulos)


def _codigo_fabrica(valor: str) -> str:
    v = valor.strip().upper()
    return v.lstrip("0") or v


def _chave(*partes) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in partes).encode("utf-8")).hexdigest()


def analisar(db: sqlite3.Connection, empresa_id: int, fabricas_permitidas: list[dict], aba: dict,
             mapeamento: dict, nome_arquivo: str = "") -> dict:
    """Interpreta a aba segundo o mapeamento e compara com o banco. Não grava nada."""
    linhas, formulas = aba["linhas"], aba.get("formulas", set())
    cab = int(mapeamento.get("linha_cabecalho") or 0)
    col = {c: mapeamento.get("colunas", {}).get(c) for c in CAMPOS}
    layout = mapeamento.get("layout", "largo")
    tipo_valor = mapeamento.get("tipo_valor", "movimento")
    data_ref = mapeamento.get("data_referencia")
    ambiguas_modo = mapeamento.get("ambiguas", "revisar")
    etapas_cat = {e["id"]: e for e in catalogo(db, empresa_id, incluir_inativas=True)}
    mapa_etapas = [m for m in mapeamento.get("etapas", []) if m.get("etapa_id") in etapas_cat]

    erros: list[dict] = []
    avisos: list[str] = []
    fabricas_por_codigo = {_codigo_fabrica(f["codigo"]): f for f in fabricas_permitidas}
    fabrica_padrao = next((f for f in fabricas_permitidas if f["id"] == mapeamento.get("fabrica_padrao_id")), None)

    if col["remessa"] is None:
        erros.append({"linha": cab + 1, "coluna": None, "mensagem": "Mapeie a coluna do número da remessa."})
    if col["fabrica"] is None and fabrica_padrao is None:
        erros.append({"linha": cab + 1, "coluna": None,
                      "mensagem": "Mapeie a coluna de fábrica ou escolha a fábrica deste arquivo."})
    if layout == "largo" and not mapa_etapas:
        avisos.append("Nenhuma coluna de seção mapeada: só o cadastro de remessas/OPs será importado.")
    if layout == "longo" and (col["etapa"] is None or (col["data"] is None and col["quantidade"] is None)):
        erros.append({"linha": cab + 1, "coluna": None,
                      "mensagem": "No formato por movimento, mapeie a coluna de seção e a de data ou quantidade."})
    if tipo_valor == "acumulado":
        try:
            data_ref = data_iso(data_ref)
        except ValueError:
            data_ref = None
        if not data_ref:
            erros.append({"linha": None, "coluna": None,
                          "mensagem": "Informe a data de referência das quantidades acumuladas."})
    if any(e["mensagem"].startswith("Mapeie") or "data de referência" in e["mensagem"] or "formato" in e["mensagem"]
           for e in erros):
        return _resultado(nome_arquivo, aba, cab, [], {}, [], erros, avisos, {}, 0, 0, 0, [], [])

    ops: dict[tuple, dict] = {}
    brutas: list[dict] = []
    total_ignoradas = vazias = linhas_dados = 0
    linhas_erro: set[int] = set()

    def erro(n: int, coluna, mensagem: str):
        erros.append({"linha": n + 1, "coluna": coluna, "mensagem": mensagem})
        linhas_erro.add(n + 1)

    for n in range(cab + 1, len(linhas)):
        linha = linhas[n]
        if not any(v not in (None, "") for v in linha):
            vazias += 1
            continue
        if _eh_total(linha, n in formulas, col["remessa"]):
            total_ignoradas += 1
            continue
        linhas_dados += 1
        remessa = texto(_valor(linha, col["remessa"]))
        if not remessa:
            erro(n, col["remessa"], "Número da remessa ausente.")
            continue
        if col["fabrica"] is not None:
            cod = texto(_valor(linha, col["fabrica"]))
            fabrica = fabricas_por_codigo.get(_codigo_fabrica(cod)) if cod else None
            if fabrica is None:
                erro(n, col["fabrica"], f"Fábrica '{cod}' não cadastrada ou sem permissão." if cod
                     else "Fábrica ausente.")
                continue
        else:
            fabrica = fabrica_padrao
        tipo = texto(_valor(linha, col["tipo_remessa"]))
        op_num = texto(_valor(linha, col["op"]))
        try:
            qtd_total = inteiro(_valor(linha, col["quantidade_total"]))
            if qtd_total is not None and qtd_total < 0:
                raise ValueError
        except (ValueError, TypeError):
            erro(n, col["quantidade_total"], f"Quantidade total inválida: {_valor(linha, col['quantidade_total'])!r}.")
            continue
        chave_op = (fabrica["id"], tipo, remessa, op_num)
        pedido = texto(_valor(linha, col["pedido"]))
        material = texto(_valor(linha, col["material"]))
        if chave_op not in ops:
            ops[chave_op] = {"fabrica_id": fabrica["id"], "fabrica_codigo": fabrica["codigo"], "tipo_remessa": tipo,
                             "remessa": remessa, "op": op_num, "pedido": pedido, "material": material,
                             "quantidade_total": qtd_total, "linhas": [n + 1], "conflitos": []}
        else:
            registro = ops[chave_op]
            registro["linhas"].append(n + 1)
            for campo, valor in (("pedido", pedido), ("material", material), ("quantidade_total", qtd_total)):
                if valor not in (None, "") and registro[campo] not in (None, "") and registro[campo] != valor:
                    registro["conflitos"].append(f"L{n + 1}: {campo} {valor!r} ≠ {registro[campo]!r}")
                elif registro[campo] in (None, "") and valor not in (None, ""):
                    registro[campo] = valor

        def adicionar(etapa_id: int, bruto_data, bruto_qtd, coluna_data, coluna_qtd, id_mov: str):
            try:
                data = data_iso(bruto_data)
            except (ValueError, TypeError):
                erro(n, coluna_data, f"Data inválida em {etapas_cat[etapa_id]['nome']}: {bruto_data!r}.")
                return
            try:
                qtd = inteiro(bruto_qtd)
                if qtd is not None and qtd < 0:
                    raise ValueError
            except (ValueError, TypeError):
                erro(n, coluna_qtd, f"Quantidade inválida em {etapas_cat[etapa_id]['nome']}: {bruto_qtd!r}.")
                return
            if data is None and qtd is None:
                return
            if tipo_valor == "acumulado":
                if qtd in (None, 0) and data is None:
                    return  # zero sem data = nada produzido ainda; não gera leitura
                data = data or data_ref
            brutas.append({"linha": n + 1, "chave_op": chave_op, "etapa_id": etapa_id, "data": data,
                           "quantidade": qtd, "id_mov": id_mov})

        id_mov = texto(_valor(linha, col["id_movimento"]))
        if layout == "longo":
            nome_etapa = texto(_valor(linha, col["etapa"]))
            if not nome_etapa:
                erro(n, col["etapa"], "Seção ausente.")
                continue
            alvo = normalizar(nome_etapa)
            etapa = next((e for e in etapas_cat.values() if alvo in {normalizar(e["nome"]), normalizar(e["codigo"]),
                          *[normalizar(s) for s in e["sinonimos"]]}), None)
            if etapa is None:
                erro(n, col["etapa"], f"Seção '{nome_etapa}' não cadastrada (cadastre a etapa ou um sinônimo).")
                continue
            adicionar(etapa["id"], _valor(linha, col["data"]), _valor(linha, col["quantidade"]), col["data"],
                      col["quantidade"], id_mov)
        else:
            for m in mapa_etapas:
                adicionar(m["etapa_id"], _valor(linha, m.get("coluna_data")), _valor(linha, m.get("coluna_quantidade")),
                          m.get("coluna_data"), m.get("coluna_quantidade"), id_mov)

    # ---------------- chaves estáveis, duplicidades e ambiguidades dentro do arquivo
    grupos: dict[str, list[dict]] = defaultdict(list)
    for b in brutas:
        fab, tipo, rem, op = b["chave_op"]
        if b["id_mov"]:
            chave = _chave("id", fab, tipo, rem, op, b["etapa_id"], b["id_mov"])
        else:
            chave = _chave(tipo_valor, fab, tipo, rem, op, b["etapa_id"], b["data"] or "")
        b["chave"] = chave
        grupos[chave].append(b)
    leituras, duplicidades, ambiguas = [], [], []
    for chave, itens in grupos.items():
        quantidades = {i["quantidade"] for i in itens}
        if len(itens) == 1 or len(quantidades) == 1:
            leituras.append(itens[0])
            if len(itens) > 1:
                duplicidades.append({"linhas": [i["linha"] for i in itens],
                                     "mensagem": "Linhas repetidas do mesmo evento: contadas uma única vez."})
        elif ambiguas_modo == "distintas" and tipo_valor == "movimento":
            for seq, item in enumerate(sorted(itens, key=lambda x: x["linha"]), start=1):
                item["chave"] = _chave(chave, "seq", seq)
                leituras.append(item)
        else:
            ambiguas.append({"linhas": [i["linha"] for i in itens], "quantidades": sorted(q for q in quantidades if q is not None),
                             "mensagem": "Mesma OP/seção/data com quantidades diferentes: revise antes de importar."})

    for registro in ops.values():
        if registro["conflitos"]:
            avisos.append(f"Remessa {registro['remessa']} OP {registro['op'] or '-'}: dados divergentes entre linhas "
                          f"({'; '.join(registro['conflitos'][:3])}). Foi usado o primeiro valor.")
    if tipo_valor == "movimento" and any(l["data"] is None for l in leituras):
        avisos.append("Há leituras sem data: serão registradas, mas não entram em relatórios por período.")

    comparacao = _comparar(db, empresa_id, ops, leituras, linhas_erro)
    return _resultado(nome_arquivo, aba, cab, leituras, ops, duplicidades, erros, avisos, comparacao,
                      linhas_dados, total_ignoradas, vazias, ambiguas, sorted(linhas_erro))


def _comparar(db: sqlite3.Connection, empresa_id: int, ops: dict, leituras: list[dict], linhas_erro: set) -> dict:
    remessas_novas, remessas_exist, ops_novas, ops_exist, ops_alteradas = {}, {}, [], [], []
    for chave, o in ops.items():
        fab, tipo, rem, op = chave
        r = db.execute("SELECT * FROM remessas WHERE empresa_id = ? AND fabrica_id = ? AND tipo_remessa = ? AND numero = ?",
                       (empresa_id, fab, tipo, rem)).fetchone()
        chave_rem = (fab, tipo, rem)
        if r is None:
            remessas_novas.setdefault(chave_rem, {"fabrica_id": fab, "tipo_remessa": tipo, "numero": rem,
                                                  "quantidade": 0, "materiais": set()})
            remessas_novas[chave_rem]["quantidade"] += o["quantidade_total"] or 0
            if o["material"]:
                remessas_novas[chave_rem]["materiais"].add(o["material"])
            o["remessa_id"] = None
        else:
            remessas_exist[chave_rem] = dict(r)
            o["remessa_id"] = r["id"]
        o["op_id"] = None
        if not op:
            continue
        existente = None
        if o["remessa_id"]:
            existente = db.execute("SELECT * FROM ops WHERE empresa_id = ? AND remessa_id = ? AND numero = ?",
                                   (empresa_id, o["remessa_id"], op)).fetchone()
        if existente is None:
            ops_novas.append(o)
        else:
            o["op_id"] = existente["id"]
            ops_exist.append(o)
            mudancas = {}
            for campo, coluna in (("pedido", "pedido"), ("material", "material"), ("quantidade_total", "quantidade_prevista")):
                novo = o[campo]
                if novo not in (None, "") and novo != existente[coluna]:
                    mudancas[coluna] = {"antes": existente[coluna], "depois": novo}
            if mudancas:
                o["mudancas"] = mudancas
                ops_alteradas.append(o)

    novas, conhecidas, alteradas = [], [], []
    for l in leituras:
        existente = db.execute("SELECT id, quantidade, data_leitura FROM leituras WHERE empresa_id = ? AND chave_origem = ?",
                               (empresa_id, l["chave"])).fetchone()
        if existente is None:
            novas.append(l)
        elif existente["quantidade"] == l["quantidade"] and (existente["data_leitura"] or None) == (l["data"] or None):
            l["leitura_id"] = existente["id"]
            conhecidas.append(l)
        else:
            l["leitura_id"] = existente["id"]
            l["antes"] = {"quantidade": existente["quantidade"], "data": existente["data_leitura"]}
            alteradas.append(l)
    return {"remessas_novas": list(remessas_novas.values()), "remessas_existentes": list(remessas_exist.values()),
            "ops_novas": ops_novas, "ops_existentes": ops_exist, "ops_alteradas": ops_alteradas,
            "leituras_novas": novas, "leituras_conhecidas": conhecidas, "leituras_alteradas": alteradas}


def _resultado(nome_arquivo, aba, cab, leituras, ops, duplicidades, erros, avisos, comp, linhas_dados,
               total_ignoradas, vazias, ambiguas, linhas_erro) -> dict:
    comp = comp or {}
    fabricas = sorted({o["fabrica_codigo"] for o in ops.values()})
    resumo = {
        "arquivo": nome_arquivo, "aba": aba["nome"], "linha_cabecalho": cab + 1,
        "total_linhas_lidas": len(aba["linhas"]), "linhas_de_dados": linhas_dados,
        "linhas_total_ignoradas": total_ignoradas, "linhas_vazias": vazias,
        "ops_identificadas": sum(1 for o in ops.values() if o["op"]),
        "remessas_identificadas": len({(o["fabrica_id"], o["tipo_remessa"], o["remessa"]) for o in ops.values()}),
        "fabricas_encontradas": fabricas,
        "remessas_novas": len(comp.get("remessas_novas", [])),
        "remessas_existentes": len(comp.get("remessas_existentes", [])),
        "ops_novas": len(comp.get("ops_novas", [])), "ops_existentes": len(comp.get("ops_existentes", [])),
        "ops_alteradas": len(comp.get("ops_alteradas", [])),
        "leituras_identificadas": len(leituras), "leituras_novas": len(comp.get("leituras_novas", [])),
        "leituras_conhecidas": len(comp.get("leituras_conhecidas", [])),
        "leituras_alteradas": len(comp.get("leituras_alteradas", [])),
        "duplicidades": len(duplicidades), "ambiguas": len(ambiguas),
        "linhas_com_erro": len(linhas_erro), "erros": len(erros), "avisos": len(avisos),
    }
    return {"resumo": resumo, "erros": erros[:500], "avisos": avisos[:200], "duplicidades": duplicidades[:200],
            "ambiguas": ambiguas[:200], "linhas_com_erro": linhas_erro,
            "_interno": {"ops": ops, "leituras": leituras, "comparacao": comp}}


def amostra_aba(aba: dict, limite: int = 15) -> list[list[str]]:
    return [[texto(c) for c in linha] for linha in aba["linhas"][:limite]]


def detalhar_para_previa(analise: dict, limite: int = 300) -> dict:
    """Listas legíveis para a interface (sem estruturas internas)."""
    comp = analise["_interno"]["comparacao"] or {}

    def op_txt(o):
        return {"fabrica": o["fabrica_codigo"], "tipo_remessa": o["tipo_remessa"], "remessa": o["remessa"],
                "op": o["op"], "pedido": o["pedido"], "material": o["material"],
                "quantidade_total": o["quantidade_total"], "linhas": o["linhas"][:10]}

    ops = analise["_interno"]["ops"]

    def leit_txt(l, extra=None):
        o = ops[l["chave_op"]]
        d = {"linha": l["linha"], "fabrica": o["fabrica_codigo"], "remessa": o["remessa"], "op": o["op"],
             "etapa_id": l["etapa_id"], "data": l["data"], "quantidade": l["quantidade"], "id_movimento": l["id_mov"]}
        if extra:
            d.update(extra)
        return d

    return {
        "ops_novas": [op_txt(o) for o in comp.get("ops_novas", [])[:limite]],
        "ops_alteradas": [dict(op_txt(o), mudancas=o.get("mudancas")) for o in comp.get("ops_alteradas", [])[:limite]],
        "remessas_novas": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()}
                           for r in comp.get("remessas_novas", [])[:limite]],
        "leituras_novas": [leit_txt(l) for l in comp.get("leituras_novas", [])[:limite]],
        "leituras_alteradas": [leit_txt(l, {"antes": l.get("antes")}) for l in comp.get("leituras_alteradas", [])[:limite]],
    }
