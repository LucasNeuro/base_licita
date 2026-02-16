# 🚀 PNCP Licitações API

API completa para extração automática de licitações do Portal Nacional de Contratações Públicas (PNCP) com salvamento no Supabase.

## ✨ Funcionalidades

✅ **Extração Automática** - Scheduler que roda todo dia no horário configurado  
✅ **Extração Manual** - Endpoint para buscar com filtros personalizados  
✅ **Console Bonito** - Visualização com Rich (barras de progresso, tabelas, cores)  
✅ **Dados Completos** - Busca itens, documentos e histórico de cada licitação  
✅ **Link do Portal** - Salva URL da página web do edital  
✅ **Sem Duplicatas** - Atualiza automaticamente se já existe  
✅ **Persistência** - Configuração salva no Supabase  
✅ **6 Modalidades** - Leilão, Concorrência, Pregão, Dispensa, Inexigibilidade  
✅ **Swagger UI** - Interface interativa para testar  

---

## 🚀 Quick Start Local

```bash
# 1. Clone
git clone https://github.com/LucasNeuro/base_licita.git
cd base_licita

# 2. Instale
pip install -r requirements.txt

# 3. Configure .env
# Crie arquivo .env com suas credenciais Supabase

# 4. Execute
python run.py

# 5. Acesse
http://localhost:8000/docs
```

---

## 🌐 Deploy no Render

1. Conecte este repositório no Render (Blueprint com `render.yaml` ou crie um Web Service).
2. Configure variáveis de ambiente no dashboard (veja **Checklist produção** abaixo).
3. Deploy automático! ✅

---

## ✅ Checklist produção (Scheduler + Extração + Classificação)

Para **tudo** funcionar em produção (extração, scheduler e classificação por IA):

| Variável | Obrigatória para | Onde configurar |
|----------|------------------|-----------------|
| `SUPABASE_URL` | Extração, Scheduler, Classificação | Render → Environment |
| `SUPABASE_KEY` | Extração, Scheduler, Classificação | Render → Environment (use **service_role**) |
| `MISTRAL_API_KEY` | Classificação (manual e automática após extração) | Render → Environment |

- **Extração:** funciona com Supabase configurado; pode ser manual (`POST /extrair/manual`) ou automática (scheduler).
- **Scheduler:** carrega e persiste a config na tabela `scheduler_horario` (id=1). Ative com `POST /scheduler/configurar` (ativo: true, horario, modalidades, dias_atras).
- **Classificação:** exige Supabase + `MISTRAL_API_KEY`. Manual: `POST /classificar/manual` ou `POST /classificar/todas`. Automática: após cada extração agendada, se houver licitações novas e Mistral configurado.

**Verificar status:** `GET /` mostra `supabase.conectado`, `classificacao_ia.disponivel` e `scheduler.ativo`. Use `GET /scheduler/status` para próxima execução.

---

## 📋 Fluxo quando estiver deployado no Render

### Ao subir o serviço (startup)

1. A API inicia (`uvicorn main:app`).
2. Conecta ao Supabase usando `SUPABASE_URL` e `SUPABASE_KEY`.
3. **Carrega a configuração do scheduler** da tabela `scheduler_horario` (registro `id = 1`) no banco.
4. Se no banco estiver **ativo = true**, o **APScheduler** é ativado e agenda a tarefa diária no horário salvo (ex.: 06:00).
5. A partir daí a API fica ouvindo em `https://seu-app.onrender.com`.

### Uso pelo Swagger (ou qualquer cliente HTTP)

- **Docs:** `GET https://seu-app.onrender.com/docs`
- **Extrair licitações:** `POST /extrair/manual` (body: `dias_atras`, `modalidades`, `uf`, `limite_paginas`, etc.).
- **Classificar N licitações:** `POST /classificar/manual` (body: `limite`, ex. 50).
- **Classificar todas as pendentes:** `POST /classificar/todas` (sem body).
- **Configurar o scheduler:** `POST /scheduler/configurar` (horário, ativo, modalidades, dias_atras, limite_paginas). A configuração é **salva no Supabase** (tabela `scheduler_horario`), então persiste entre deploys e reinícios.
- **Status do scheduler:** `GET /scheduler/status`.
- **Estatísticas:** `GET /estatisticas`.

### Execução automática diária (quando o scheduler está ativo)

1. No **horário configurado** (ex.: 06:00), o APScheduler dispara `tarefa_extracao_automatica`.
2. A API chama o PNCP, busca licitações conforme modalidades/dias_atras/limite salvos no banco, grava em `public.licitacoes` e atualiza `scheduler_horario` (última e próxima execução).
3. Se **MISTRAL_API_KEY** estiver configurada e houver licitações novas, em seguida roda a **classificação automática** (todas as pendentes de `subsetor_principal_id`), gravando em `licitacoes_classificacao` e atualizando `licitacoes`.

### Observação importante (plano Free do Render)

No plano **Free**, o serviço pode **dormir** após ~15 min sem requisições. Enquanto estiver dormindo, o scheduler **não roda** (não há processo ativo para executar o horário). Opções:

- **Acordar antes do horário:** usar um **Cron Job** externo (ex.: cron-job.org, Uptime Robot) para chamar `GET /` ou `GET /scheduler/status` alguns minutos antes do horário (ex.: 05:55), assim o serviço acorda e o scheduler dispara no horário.
- Ou fazer um **Cron Job no Render** (se disponível no seu plano) que chame `POST /extrair/manual` no horário desejado.
- Em **planos pagos** (serviço sempre ligado), o scheduler roda no horário configurado sem precisar de truques.

---

## 📚 Endpoints

- `GET /` - Status da API (Supabase, classificação IA, scheduler)
- `GET /health` - Health check (200 se a API está no ar)
- `GET /docs` - Swagger UI
- `GET /config` - Ver configurações
- `POST /extrair/manual` - Extração manual
- `POST /scheduler/configurar` - Configurar scheduler (persiste no Supabase)
- `GET /scheduler/status` - Status do scheduler e próxima execução
- `POST /classificar/manual` - Classificar N licitações (IA)
- `POST /classificar/todas` - Classificar todas as pendentes (IA)
- `GET /estatisticas` - Estatísticas

---

## 🎯 Exemplo de Uso

```json
POST /extrair/manual
{
  "dias_atras": 1,
  "modalidades": null,
  "limite_paginas": null
}
```

Busca TODAS as licitações de ontem, salva no Supabase!

---

## ⏰ Scheduler Automático

```json
POST /scheduler/configurar
{
  "horario": "06:00",
  "ativo": true,
  "modalidades": [1, 4, 6, 7, 8, 9],
  "dias_atras": 1,
  "limite_paginas": null
}
```

TODO DIA às 06:00 busca automaticamente!

---

## 🛠️ Stack

- FastAPI
- Supabase (PostgreSQL)
- APScheduler
- Rich (Console)
- Python 3.11

---

**Desenvolvido com ❤️ para facilitar a coleta de licitações públicas!**
