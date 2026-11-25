#!/usr/bin/env python3
"""Coleta do Diário Oficial da União para o monitor de editais de filosofia.

O portal público do DOU recusa consultas automatizadas vindas dos servidores do GitHub,
e o robô não contorna essa recusa. A leitura é feita pelo INLABS, o serviço oficial da
Imprensa Nacional para download automatizado do DOU em XML, com a conta gratuita do
dono do repositório (segredos INLABS_EMAIL e INLABS_SENHA). Sem esses segredos, o robô
registra que aguarda as credenciais e termina sem erro.

Para cada dia, baixa a Seção 3 (e a edição extra, quando há), separa os editais
docentes de universidades federais, institutos federais, CEFETs e Colégio Pedro II e
marca os que citam Filosofia, Ciências Humanas ou áreas de Educação ligadas à filosofia.

Saída:
  dados/dou/AAAA-MM-DD.json  um arquivo por dia
  dados/dou/recentes.json    os oito dias mais recentes, sem repetições

Uso:
  python coletor/dou.py                 hoje (horário de Brasília) e os 2 dias anteriores
  python coletor/dou.py --data 2026-11-25 --retro 0
"""
import argparse
import datetime as dt
import glob
import io
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
INLABS = "https://inlabs.in.gov.br"
SECOES = ["DO3", "DO3E"]
RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SAIDA = os.path.join(RAIZ, "dados", "dou")
MANTER_DIAS = 30
DIAS_RECENTES = 8
UA = ("monitor-editais-filosofia/1.0 (+https://github.com/matheusdosreisgomess-collab/"
      "monitor-editais-filosofia; coleta diaria de editais docentes)")


def dobra(c):
    """Um caractere para um caractere minúsculo sem acento (preserva posições)."""
    base = unicodedata.normalize("NFD", c).encode("ascii", "ignore").decode()
    return (base[:1] or " ").lower()


def norm(s):
    return "".join(dobra(c) for c in (s or ""))


def texto_html(s):
    if not s:
        return ""
    txt = BeautifulSoup(s, "html.parser").get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", txt)


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


def entra(email, senha):
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.post(f"{INLABS}/logar.php", data={"email": email, "password": senha},
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    r.raise_for_status()
    cookie = s.cookies.get("inlabs_session_cookie")
    if not cookie:
        raise RuntimeError("login no INLABS sem cookie de sessão (confira e-mail e senha)")
    return s, cookie


def baixa_secao(s, cookie, dia, secao):
    """Atos de uma seção em um dia, a partir do zip de XMLs do INLABS. None se não houver edição."""
    d = dia.isoformat()
    r = s.get(f"{INLABS}/index.php", params={"p": d, "dl": f"{d}-{secao}.zip"},
              headers={"Cookie": f"inlabs_session_cookie={cookie}", "origem": "736372697074"}, timeout=180)
    if r.status_code == 404 or not r.content.startswith(b"PK"):
        return None
    r.raise_for_status()
    atos = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for nome in z.namelist():
            if nome.lower().endswith(".xml"):
                atos += le_xml(z.read(nome))
    return atos


def le_xml(dados):
    atos = []
    try:
        raiz = ET.fromstring(dados)
    except ET.ParseError:
        return atos
    for art in raiz.iter("article"):
        corpo = {(f.tag or "").lower(): (f.text or "") for f in art.find("body") or []}
        atos.append({
            "titulo": " ".join(texto_html(corpo.get("identifica", "")).split()),
            "ementa": texto_html(corpo.get("ementa", "")),
            "texto": texto_html(corpo.get("texto", "")),
            "orgao": art.get("artCategory", ""),
            "tipo_ato": art.get("artType", ""),
            "data": art.get("pubDate", ""),
            "secao": art.get("pubName", ""),
            "pagina": art.get("numberPage", ""),
            "url": art.get("pdfPage", ""),
        })
    return atos


def eh_edital_docente(ato):
    org = norm(ato["orgao"])
    if "ministerio da educacao" not in org or not ORGAO.search(org):
        return False
    titulo = norm(ato["titulo"] + " " + ato["ementa"])
    corpo = norm(ato["texto"][:1500])
    if EXCLUI.search(titulo + " " + corpo[:240]):
        return False
    tudo = titulo + " " + corpo
    return bool(DOCENTE.search(tudo) and (SELECAO.search(tudo) or "edital" in norm(ato["tipo_ato"])))


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


def classifica(dia, atos, consulta):
    rel = {"data": dia.isoformat(), "gerado_em": dt.datetime.now(TZ).isoformat(timespec="seconds"),
           "consulta": consulta, "relevantes": [], "docentes_sem_area": [], "erros": []}
    cand = [a for a in atos if eh_edital_docente(a)]
    consulta["docentes"] = len(cand)
    for a in cand:
        base = {k: a[k] for k in ("titulo", "orgao", "tipo_ato", "data", "secao", "pagina", "url")}
        base["instituicao"] = instituicao(a["orgao"])
        txt = a["texto"]
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


def consolida(aviso=None):
    dias = sorted(glob.glob(os.path.join(SAIDA, "????-??-??.json")))
    for velho in dias[:-MANTER_DIAS]:
        os.remove(velho)
    dias = dias[-MANTER_DIAS:]
    recentes = {"gerado_em": dt.datetime.now(TZ).isoformat(timespec="seconds"), "dias": [],
                "relevantes": [], "docentes_sem_area": [], "erros": [aviso] if aviso else []}
    vistos = set()
    for p in reversed(dias[-DIAS_RECENTES:]):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        recentes["dias"].append({"data": d.get("data"), "consulta": d.get("consulta", {})})
        recentes["erros"] += [f"{d.get('data')}: {e}" for e in d.get("erros", [])]
        for chave in ("relevantes", "docentes_sem_area"):
            for item in d.get(chave, []):
                chave_item = (item.get("url", ""), item.get("titulo", ""), item.get("orgao", ""))
                if chave_item in vistos:
                    continue
                vistos.add(chave_item)
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
    email, senha = os.environ.get("INLABS_EMAIL", "").strip(), os.environ.get("INLABS_SENHA", "")
    if not email or not senha:
        aviso = "Aguardando credenciais do INLABS (segredos INLABS_EMAIL e INLABS_SENHA); DOU não coletado"
        print(aviso)
        consolida(aviso)
        return 0
    try:
        s, cookie = entra(email, senha)
    except Exception as e:  # noqa: BLE001
        aviso = f"Falha no login do INLABS: {type(e).__name__}: {str(e)[:200]}"
        print(aviso)
        consolida(aviso)
        return 1
    for k in range(max(0, a.retro), -1, -1):
        dia = fim - dt.timedelta(days=k)
        atos, consulta = [], {"metodo": "inlabs", "ok": True, "secoes": {}}
        for secao in SECOES:
            try:
                lista = baixa_secao(s, cookie, dia, secao)
                consulta["secoes"][secao] = "sem edição" if lista is None else len(lista)
                atos += lista or []
            except Exception as e:  # noqa: BLE001
                consulta["secoes"][secao] = f"erro {type(e).__name__}"
                if secao == "DO3":
                    consulta["ok"] = False
            time.sleep(1)
        consulta["atos"] = len(atos)
        rel = classifica(dia, atos, consulta)
        print(f"{dia}: {consulta} relevantes={len(rel['relevantes'])} sem_area={len(rel['docentes_sem_area'])}")
        caminho = os.path.join(SAIDA, f"{dia.isoformat()}.json")
        if consulta["ok"] or not os.path.exists(caminho):
            grava(rel, caminho)
    r = consolida()
    print(f"recentes: relevantes={len(r['relevantes'])} sem_area={len(r['docentes_sem_area'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
