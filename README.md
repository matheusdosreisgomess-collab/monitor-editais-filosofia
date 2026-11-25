# Monitor de editais de filosofia

Robô que lê todo dia a Seção 3 do Diário Oficial da União e separa editais docentes de universidades federais, institutos federais, CEFETs e Colégio Pedro II ligados a três eixos: Filosofia, Ciências Humanas e Educação (Filosofia da Educação, Fundamentos da Educação e afins).

O resultado alimenta a página **Editais de Filosofia**, atualizada por uma varredura diária do Claude, que lê estes arquivos, confere cada edital e avisa no celular quando surge algo novo.

## Como funciona

- `coletor/dou.py` baixa a edição do dia (Seção 3 e edição extra, quando há), filtra os atos de órgãos do Ministério da Educação que tratam de concurso ou seleção de professor, abre o texto integral de cada um e marca os que citam filosofia, ciências humanas ou fundamentos da educação.
- `.github/workflows/coleta-dou.yml` roda o coletor às 5h17 e às 18h17 (horário de Brasília) e grava os arquivos em `dados/dou/`.
- `dados/dou/recentes.json` reúne os três dias mais recentes:
  - `relevantes`: atos com sinais dos três eixos, com trechos e texto;
  - `docentes_sem_area`: editais docentes cujo extrato não informa a área, para conferência no edital completo.

## Rodar à mão

Na aba **Actions**, escolha **Coleta DOU** e use **Run workflow**. O campo *retro* define quantos dias anteriores reprocessar.

Localmente:

```bash
pip install -r requirements.txt
python coletor/dou.py --retro 2
```

## Limites

Universidades estaduais publicam nos diários de cada estado e nos próprios portais; essa parte fica com a varredura diária do Claude (agregadores, ANPOF e portais institucionais). Os arquivos aqui são um resumo automático: o texto oficial do DOU e o edital completo prevalecem.
