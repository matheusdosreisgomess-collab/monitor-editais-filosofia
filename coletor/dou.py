#!/usr/bin/env python3
"""Coleta do Diário Oficial da União para o monitor de editais de filosofia.

Para cada dia, consulta a busca do DOU com termos dos três eixos (Filosofia, Ciências
Humanas, Educação) e termos gerais de seleção docente, separa os editais de
universidades federais, institutos federais, CEFETs e Colégio Pedro II, abre o texto
integral de cada um e marca os que citam filosofia, ciências humanas ou áreas de
educação ligadas à filosofia. Se a busca falhar, tenta a página de leitura do jornal.

Saída:
  dados/dou/AAAA-MM-DD.json  um arquivo por dia
  dados/dou/recentes.json    os oito dias mais recentes, sem repetições

Uso:
  python coletor/dou.py                 hoje (horário de Brasília) e os 2 dias anteriores
  python coletor/dou.py --retro 0       só hoje
  python coletor/dou.py --data 2026-11-25
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import unicodedata
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
BASE = "https://www.in.gov.br"
RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SAIDA = os.path.join(RAIZ, "dados", "dou")
MANTER_DIAS = 30
DIAS_RECENTES = 8
PAUSA = 1.0
POR_PAGINA = 75
MAX_PAGINAS = 3
MAX_TEXTOS = 160
TERMOS = [
    '"filosofia"', '"ciências humanas"', '"fundamentos da educação"', '"humanidades"',
    '"professor substituto"', '"professor visitante"', '"magistério superior"',
    '"processo seletivo simplificado"',
]
HDRS = {
    "User-Agent": "monitor-editais-filosofia/1.0 (+https://github.com/matheusdosreisgomess-collab/"
                  "monitor-editais-filosofia; coleta diaria de editais docentes)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}


def dobra(c):
    """Um caractere para um caractere minúsculo sem acento (preserva posições)."""
    base = unicodedata.normalize("NFD", c).encode("ascii", "ignore").decode()
    return (base[:1] or " ").lower()


def norm(s):
    return "".join(dobra(c) for c in (s or ""))


def sem_tags(s):
    return re.sub(r"<[^>]+>", " ", str(s or ""))


ORGAO = re.compile(r"universidade|instituto federal|centro federal|colegio pedro ii")
DOCENTE = re.compile(r"professor|docente|magisterio|ebtt|ensino basico, tecnico")
SELECAO = re.compile(r"concurso|processo seletivo|selecao")
EXCLUI = re.compile(
    r"homologa|resultado|convoca|nomea|prorroga|retifica|cancela|revoga|torna sem efeito|"
    r"extrato de contrato|aditivo|pregao|licitac|dispensa de|inexigibilidade|redistribui"
)
ROTULO_AREA = re.compile(
    r"\barea(s)?( de conhecimento| de concentracao| do concurso| de atuacao| do processo seletivo)?\s*[:\-]"
    r"|componente curricular|disciplina(s)?\s*[:\-]|setor de estudo"
)
SINAIS = {
    "filosofia": re.compile(r"filosof"),
    "humanas": re.compile(r"ciencias humanas|humanidades"),
    "educacao": re.compile(
        r"fundamentos (da|historicos|filosoficos|sociologicos|socio)[^.;\n]{0,60}educacao"
        r"|filosofia da educacao|fundamentos filosoficos|politicas educacionais|didatica"
    ),
}


def sessao():
    s = requests.Session()
    s.headers.update(HDRS)
    return s


def sonda(nome, s, dia):
    """Testa a busca e a leitura do jornal para um dia e diz se alguma devolve dados."""
    d = dia.strftime("%d-%m-%Y")
    alvos = [
        ("busca", f"{BASE}/consulta/-/buscar/dou",
         {"q": '"professor"', "s": "todos", "exactDate": "personalizado", "publishFrom": d, "publishTo": d, "sortType": "0"}),
        ("leitura", f"{BASE}/leiturajornal", {"data": d, "secao": "do3"}),
    ]
    ok = False
    for rotulo, url, params in alvos:
        try:
            r = s.get(url, params=params, timeout=45)
            arr = json_array(r.text) if r.status_code == 200 else None
            extra = f"{len(arr)} itens" if arr is not None else f"início: {' '.join(r.text[:160].split())!r}"
            print(f"  sonda {nome} {rotulo} {d}: HTTP {r.status_code}, server={r.headers.get('server')}, "
                  f"{len(r.text)} bytes, jsonArray={'sim' if arr is not None else 'não'}, {extra}")
            ok = ok or arr is not None
        except Exception as e:  # noqa: BLE001
            print(f"  sonda {nome} {rotulo} {d}: {type(e).__name__}: {str(e)[:160]}")
    return ok


def baixa(s, url, params=None):
    ultimo = None
    for t in range(2):
        try:
            r = s.get(url, params=params, timeout=45)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(5)
    raise ultimo


def json_array(html):
    soup = BeautifulSoup(html, "html.parser")
    for sc in soup.find_all("script"):
        txt = sc.string or sc.get_text() or ""
        if "jsonArray" not in txt:
            continue
        try:
            return json.loads(txt).get("jsonArray") or []
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                return json.loads(m.group(0)).get("jsonArray") or []
    return None


def amostra(r):
    return f"HTTP {r.status_code}, {len(r.text)} bytes, início: {' '.join(r.text[:200].split())!r}"


def busca(s, dia, termo, diag):
    d = dia.strftime("%d-%m-%Y")
    vistos, itens = set(), []
    for pagina in range(1, MAX_PAGINAS + 1):
        params = {"q": termo, "s": "todos", "exactDate": "personalizado", "publishFrom": d,
                  "publishTo": d, "sortType": "0", "delta": str(POR_PAGINA)}
        if pagina > 1:
            params.update({"currentPage": str(pagina), "newPage": str(pagina)})
        r = baixa(s, f"{BASE}/consulta/-/buscar/dou", params)
        arr = json_array(r.text)
        if arr is None:
            diag.append(f"busca {termo} p{pagina}: sem jsonArray ({amostra(r)})")
            if pagina == 1:
                raise RuntimeError("busca sem jsonArray")
            break
        novos = [i for i in arr if i.get("urlTitle") and i.get("urlTitle") not in vistos]
        vistos.update(i.get("urlTitle") for i in novos)
        itens += novos
        diag.append(f"busca {termo} p{pagina}: {len(arr)} itens, {len(novos)} novos")
        if len(arr) < POR_PAGINA or not novos:
            break
        time.sleep(PAUSA)
    return itens


def leitura_jornal(s, dia, secao, diag):
    r = baixa(s, f"{BASE}/leiturajornal", {"data": dia.strftime("%d-%m-%Y"), "secao": secao})
    arr = json_array(r.text)
    if arr is None:
        diag.append(f"leitura {secao}: sem jsonArray ({amostra(r)})")
        return []
    diag.append(f"leitura {secao}: {len(arr)} atos")
    return arr


def texto_integral(s, url_title):
    url = f"{BASE}/web/dou/-/{url_title}"
    r = baixa(s, url)
    soup = BeautifulSoup(r.text, "html.parser")
    alvo = soup.select_one("div.texto-dou") or soup.select_one("div.materia") or soup.find("article") or soup.body or soup
    txt = alvo.get_text("\n", strip=True)
    return url, re.sub(r"\n{2,}", "\n", txt)


def eh_edital_docente(item):
    org = norm(item.get("hierarchyStr", ""))
    if "ministerio da educacao" not in org or not ORGAO.search(org):
        return False
    titulo = norm(" ".join(sem_tags(item.get(k, "")) for k in ("title", "titulo", "subTitulo")))
    corpo = norm(sem_tags(item.get("content", "")))
    if EXCLUI.search(titulo + " " + corpo[:240]):
        return False
    tudo = titulo + " " + corpo
    return bool(DOCENTE.search(tudo) and (SELECAO.search(tudo) or "edital" in norm(item.get("artType", ""))))


def trechos(txt, n, sinais, limite=4):
    saida = []
    for nome in sinais:
        for m in SINAIS[nome].finditer(n):
            a, b = max(0, m.start() - 180), min(len(txt), m.end() + 180)
            trecho = " ".join(txt[a:b].split())
            if trecho and trecho not in saida:
                saida.append(trecho)
            if len(saida) >= limite:
                return saida
    return saida


def instituicao(hier):
    partes = [p.strip() for p in (hier or "").split("/") if p.strip()]
    return partes[1] if len(partes) > 1 else (partes[0] if partes else "")


def coleta_dia(s, dia):
    rel = {"data": dia.isoformat(), "gerado_em": dt.datetime.now(TZ).isoformat(timespec="seconds"),
           "consulta": {}, "relevantes": [], "docentes_sem_area": [], "erros": [], "diagnostico": []}
    diag = rel["diagnostico"]
    itens, falhas = {}, 0
    for termo in TERMOS:
        try:
            for it in busca(s, dia, termo, diag):
                itens.setdefault(it["urlTitle"], it)
        except Exception as e:  # noqa: BLE001
            falhas += 1
            diag.append(f"busca {termo}: {type(e).__name__}: {e}"[:300])
        time.sleep(PAUSA)
    metodo = "busca"
    if falhas == len(TERMOS):
        metodo = "leitura"
        for secao in ("do3", "do3e"):
            try:
                for it in leitura_jornal(s, dia, secao, diag):
                    if it.get("urlTitle"):
                        itens.setdefault(it["urlTitle"], it)
            except Exception as e:  # noqa: BLE001
                diag.append(f"leitura {secao}: {type(e).__name__}: {e}"[:300])
    ok = bool(itens) or (metodo == "busca" and falhas < len(TERMOS))
    cand = [i for i in itens.values() if eh_edital_docente(i)][:MAX_TEXTOS]
    rel["consulta"] = {"metodo": metodo, "ok": ok, "atos": len(itens), "docentes": len(cand)}
    if not ok:
        rel["erros"].append("DOU indisponível para este dia; ver diagnostico")
    for it in cand:
        time.sleep(PAUSA)
        base = {
            "titulo": " ".join(sem_tags(it.get("title") or it.get("titulo") or "").split()),
            "orgao": it.get("hierarchyStr", ""),
            "instituicao": instituicao(it.get("hierarchyStr", "")),
            "tipo_ato": it.get("artType", ""),
            "data": it.get("pubDate", ""),
            "secao": it.get("pubName", ""),
            "pagina": it.get("numberPage", ""),
        }
        try:
            url, txt = texto_integral(s, it.get("urlTitle", ""))
        except Exception as e:  # noqa: BLE001
            base["url"] = f"{BASE}/web/dou/-/{it.get('urlTitle', '')}"
            base["resumo"] = " ".join(sem_tags(it.get("content", "")).split())[:400]
            base["erro"] = type(e).__name__
            rel["docentes_sem_area"].append(base)
            continue
        base["url"] = url
        n = norm(txt)
        sinais = [k for k, rx in SINAIS.items() if rx.search(n)]
        if sinais:
            base.update({"sinais": sinais, "trechos": trechos(txt, n, sinais), "texto": txt[:8000]})
            rel["relevantes"].append(base)
        elif not ROTULO_AREA.search(n):
            base["resumo"] = " ".join(txt.split())[:500]
            rel["docentes_sem_area"].append(base)
    return rel


def grava(obj, caminho):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def consolida():
    dias = sorted(glob.glob(os.path.join(SAIDA, "????-??-??.json")))
    for velho in dias[:-MANTER_DIAS]:
        os.remove(velho)
    dias = dias[-MANTER_DIAS:]
    recentes = {"gerado_em": dt.datetime.now(TZ).isoformat(timespec="seconds"), "dias": [],
                "relevantes": [], "docentes_sem_area": [], "erros": []}
    vistos = set()
    for p in reversed(dias[-DIAS_RECENTES:]):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        recentes["dias"].append({"data": d.get("data"), "consulta": d.get("consulta", {})})
        recentes["erros"] += [f"{d.get('data')}: {e}" for e in d.get("erros", [])]
        for chave in ("relevantes", "docentes_sem_area"):
            for item in d.get(chave, []):
                if item.get("url") in vistos:
                    continue
                vistos.add(item.get("url"))
                item = dict(item)
                if "texto" in item:
                    item["texto"] = item["texto"][:2500]
                recentes[chave].append(item)
    grava(recentes, os.path.join(SAIDA, "recentes.json"))
    return recentes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="AAAA-MM-DD; padrão: hoje em Brasília")
    ap.add_argument("--retro", type=int, default=2, help="dias anteriores a reprocessar")
    a = ap.parse_args()
    fim = dt.date.fromisoformat(a.data) if a.data else dt.datetime.now(TZ).date()
    util = fim
    while util.weekday() >= 5:
        util -= dt.timedelta(days=1)
    s = sessao()
    falhas, total = 0, max(0, a.retro) + 1
    print("Sondagem do portal do DOU:")
    if not sonda("requests", s, util):
        print("O portal do DOU não devolveu dados; a coleta fica registrada como falha e nada é contornado.")
        agora = dt.datetime.now(TZ).isoformat(timespec="seconds")
        for k in range(total):
            dia = fim - dt.timedelta(days=k)
            caminho = os.path.join(SAIDA, f"{dia.isoformat()}.json")
            if not os.path.exists(caminho):
                grava({"data": dia.isoformat(), "gerado_em": agora, "consulta": {"ok": False},
                       "relevantes": [], "docentes_sem_area": [],
                       "erros": ["O portal do DOU recusou ou não respondeu à consulta automatizada"]}, caminho)
        consolida()
        return 1
    for k in range(total - 1, -1, -1):
        dia = fim - dt.timedelta(days=k)
        rel = coleta_dia(s, dia)
        c = rel["consulta"]
        print(f"{dia}: metodo={c.get('metodo')} ok={c.get('ok')} atos={c.get('atos')} docentes={c.get('docentes')} "
              f"relevantes={len(rel['relevantes'])} sem_area={len(rel['docentes_sem_area'])}")
        for linha in rel["diagnostico"]:
            print("   ", linha)
        caminho = os.path.join(SAIDA, f"{dia.isoformat()}.json")
        if not c.get("ok"):
            falhas += 1
            if os.path.exists(caminho):
                continue
        grava(rel, caminho)
    r = consolida()
    print(f"recentes: relevantes={len(r['relevantes'])} sem_area={len(r['docentes_sem_area'])}")
    return 1 if falhas == total else 0


if __name__ == "__main__":
    sys.exit(main())
