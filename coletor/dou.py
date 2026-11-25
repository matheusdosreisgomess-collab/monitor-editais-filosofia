#!/usr/bin/env python3
"""Coleta do Diário Oficial da União (Seção 3) para o monitor de editais de filosofia.

Para cada dia, lê a edição inteira da Seção 3 (e a extra, quando houver), separa os
editais docentes de universidades federais, institutos federais, CEFETs e Colégio
Pedro II, abre o texto integral de cada um e marca os que mencionam Filosofia,
Ciências Humanas ou áreas de Educação ligadas à filosofia.

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
SECOES = ["do3", "do3e"]
RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SAIDA = os.path.join(RAIZ, "dados", "dou")
MANTER_DIAS = 30
DIAS_RECENTES = 8
PAUSA = 1.0
HDRS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}


def dobra(c):
    """Um caractere para um caractere minúsculo sem acento (preserva posições)."""
    base = unicodedata.normalize("NFD", c).encode("ascii", "ignore").decode()
    return (base[:1] or " ").lower()


def norm(s):
    return "".join(dobra(c) for c in (s or ""))


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


def leitura_jornal(s, dia, secao):
    """Lista de atos de uma seção em um dia, a partir da página de leitura do jornal."""
    r = s.get(f"{BASE}/leiturajornal", params={"data": dia.strftime("%d-%m-%Y"), "secao": secao}, timeout=90)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for sc in soup.find_all("script"):
        txt = sc.string or sc.get_text() or ""
        if "jsonArray" in txt:
            try:
                return json.loads(txt).get("jsonArray", []) or []
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", txt, re.S)
                if m:
                    return json.loads(m.group(0)).get("jsonArray", []) or []
    if "jsonArray" not in r.text:
        return []
    raise RuntimeError(f"jsonArray presente mas ilegível em {secao} {dia}")


def texto_integral(s, url_title):
    url = f"{BASE}/web/dou/-/{url_title}"
    r = s.get(url, timeout=90)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    alvo = soup.select_one("div.texto-dou") or soup.select_one("div.materia") or soup.find("article") or soup.body or soup
    txt = alvo.get_text("\n", strip=True)
    return url, re.sub(r"\n{2,}", "\n", txt)


def eh_edital_docente(item):
    org = norm(item.get("hierarchyStr", ""))
    if "ministerio da educacao" not in org or not ORGAO.search(org):
        return False
    titulo = norm(" ".join(str(item.get(k, "")) for k in ("title", "titulo", "subTitulo")))
    corpo = norm(str(item.get("content", "")))
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
           "secoes": {}, "relevantes": [], "docentes_sem_area": [], "erros": []}
    for secao in SECOES:
        try:
            itens = leitura_jornal(s, dia, secao)
        except Exception as e:  # noqa: BLE001
            rel["secoes"][secao] = {"ok": False}
            if secao == "do3":
                rel["erros"].append(f"{secao}: {type(e).__name__}: {e}"[:300])
            continue
        cand = [i for i in itens if eh_edital_docente(i)]
        rel["secoes"][secao] = {"ok": True, "atos": len(itens), "docentes": len(cand)}
        for it in cand:
            time.sleep(PAUSA)
            base = {
                "titulo": " ".join(str(it.get("title") or it.get("titulo") or "").split()),
                "orgao": it.get("hierarchyStr", ""),
                "instituicao": instituicao(it.get("hierarchyStr", "")),
                "tipo_ato": it.get("artType", ""),
                "data": it.get("pubDate", ""),
                "secao": secao,
                "pagina": it.get("numberPage", ""),
            }
            try:
                url, txt = texto_integral(s, it.get("urlTitle", ""))
            except Exception as e:  # noqa: BLE001
                base["url"] = f"{BASE}/web/dou/-/{it.get('urlTitle', '')}"
                base["resumo"] = " ".join(str(it.get("content", "")).split())[:400]
                base["erro"] = f"{type(e).__name__}"
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
    dias = sorted(p for p in glob.glob(os.path.join(SAIDA, "????-??-??.json")))
    for velho in dias[:-MANTER_DIAS]:
        os.remove(velho)
    dias = dias[-MANTER_DIAS:]
    recentes = {"gerado_em": dt.datetime.now(TZ).isoformat(timespec="seconds"), "dias": [],
                "relevantes": [], "docentes_sem_area": [], "erros": []}
    vistos = set()
    for p in reversed(dias[-DIAS_RECENTES:]):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        recentes["dias"].append({"data": d.get("data"), "secoes": d.get("secoes", {})})
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
    s = sessao()
    falhas = 0
    for k in range(max(0, a.retro), -1, -1):
        dia = fim - dt.timedelta(days=k)
        rel = coleta_dia(s, dia)
        do3 = rel["secoes"].get("do3", {})
        print(f"{dia}: do3 ok={do3.get('ok')} atos={do3.get('atos')} docentes={do3.get('docentes')} "
              f"relevantes={len(rel['relevantes'])} sem_area={len(rel['docentes_sem_area'])} erros={rel['erros']}")
        if do3.get("ok") is False:
            falhas += 1
        caminho = os.path.join(SAIDA, f"{dia.isoformat()}.json")
        if do3.get("ok") is False and os.path.exists(caminho):
            continue
        grava(rel, caminho)
    r = consolida()
    print(f"recentes: relevantes={len(r['relevantes'])} sem_area={len(r['docentes_sem_area'])}")
    return 1 if falhas == max(0, a.retro) + 1 else 0


if __name__ == "__main__":
    sys.exit(main())
