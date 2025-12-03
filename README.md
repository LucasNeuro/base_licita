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

1. Conecte este repositório no Render
2. Configure variáveis de ambiente:
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
3. Deploy automático! ✅

---

## 📚 Endpoints

- `GET /` - Status da API
- `GET /docs` - Swagger UI
- `GET /config` - Ver configurações
- `POST /extrair/manual` - Extração manual
- `POST /scheduler/configurar` - Configurar scheduler
- `GET /scheduler/status` - Status do scheduler
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
