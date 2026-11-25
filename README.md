# Monitor de editais de filosofia

Robô que lê todo dia a Seção 3 do Diário Oficial da União e separa editais docentes de universidades federais, institutos federais, CEFETs e Colégio Pedro II ligados a três eixos: Filosofia, Ciências Humanas e Educação (Filosofia da Educação, Fundamentos da Educação e afins).

O resultado alimenta a página **Editais de Filosofia**, que o Claude atualiza às terças e sextas: ele lê estes arquivos, junta outras fontes (ANPOF, agregadores, portais das universidades estaduais), confere cada edital e avisa no celular quando surge algo novo.

## Ativar a leitura do DOU (uma vez)

O portal público do DOU recusa consultas automatizadas vindas dos servidores do GitHub, e o robô não contorna essa recusa. Ele usa o **INLABS**, serviço oficial e gratuito da Imprensa Nacional para baixar o DOU em XML.

1. Crie uma conta gratuita em <https://inlabs.in.gov.br>.
2. Neste repositório, abra **Settings**, depois **Secrets and variables**, **Actions**, e use **New repository secret** duas vezes:
   - nome `INLABS_EMAIL`, com o e-mail da conta;
   - nome `INLABS_SENHA`, com a senha.
3. Em **Actions**, abra **Coleta DOU** e clique em **Run workflow** para testar.

Sem esses segredos, o robô roda, registra que aguarda as credenciais e termina sem erro.

## Como funciona

- `coletor/dou.py` baixa a Seção 3 do dia (e a edição extra, quando há), filtra os atos de órgãos do Ministério da Educação que tratam de concurso ou seleção de professor e marca os que citam filosofia, ciências humanas ou fundamentos da educação.
- `.github/workflows/coleta-dou.yml` roda o coletor todo dia às 5h17 (horário de Brasília), reprocessando os dois dias anteriores para pegar edições extras, e grava os arquivos em `dados/dou/`.
- `dados/dou/recentes.json` reúne os oito dias mais recentes:
  - `relevantes`: atos com sinais dos três eixos, com trechos e texto;
  - `docentes_sem_area`: editais docentes cujo extrato não informa a área, para conferência no edital completo.

## Limites

Universidades estaduais publicam nos diários de cada estado e nos próprios portais; essa parte fica com as rodadas do Claude. Os arquivos aqui são um resumo automático: o texto oficial do DOU e o edital completo prevalecem.
