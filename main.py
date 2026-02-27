"""
FastAPI para coletar licitações do PNCP e salvar no Supabase
Arquivo único com scheduler automático e endpoint manual
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
from supabase import create_client, Client
import logging
import copy
import asyncio

# Rich para console bonito
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from rich import box

# Importar configurações
from config import (
    SupabaseConfig,
    PNCPConfig,
    SchedulerConfig,
    ClassificacaoSchedulerConfig,
    ServerConfig,
    LogConfig,
    ModalidadesConfig,
    exibir_configuracoes,
    MistralConfig,
)

# Importar classificador
from classificador import ClassificadorIA

# Configuração de logs
logging.basicConfig(
    level=getattr(logging, LogConfig.LEVEL),
    format=LogConfig.FORMAT,
    datefmt=LogConfig.DATE_FORMAT
)
logger = logging.getLogger(__name__)

# Console Rich
console = Console()

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================

# Inicializar Supabase
supabase: Client = None
SUPABASE_ENABLED = False

if SupabaseConfig.is_configured():
    try:
        supabase = create_client(SupabaseConfig.URL, SupabaseConfig.KEY)
        SUPABASE_ENABLED = True
        logger.info("✅ Supabase conectado com sucesso!")
    except Exception as e:
        logger.error(f"❌ Erro ao conectar Supabase: {str(e)}")
        logger.warning("⚠️ A API funcionará em modo TESTE (sem salvar dados)")
else:
    logger.warning("⚠️ Credenciais do Supabase não configuradas")
    logger.warning("⚠️ A API funcionará em modo TESTE (sem salvar dados)")
    logger.warning("💡 Configure o arquivo .env com SUPABASE_URL e SUPABASE_KEY")

# ============================================================================
# MODELOS
# ============================================================================

class ConfigScheduler(BaseModel):
    """Modelo para configurar o scheduler"""
    horario: str = "06:00"  # Formato HH:MM
    ativo: bool = True
    modalidades: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]  # Todas as modalidades
    dias_atras: int = 1  # Quantos dias para trás buscar
    limite_paginas: Optional[int] = None  # None = SEM LIMITE (busca tudo!)
    
class ConfigGeral(BaseModel):
    """Modelo para configurações gerais da aplicação"""
    tamanho_pagina: int = 50  # Tamanho de página padrão (max 500)
    timeout_requisicao: int = 30  # Timeout em segundos
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

class ExtrairManualRequest(BaseModel):
    """Modelo para extração manual"""
    dias_atras: int = 1
    modalidades: Optional[List[int]] = None  # None = TODAS as modalidades
    uf: Optional[str] = None
    limite_paginas: Optional[int] = None  # None = SEM LIMITE (busca TUDO!)
    data_referencia: Optional[str] = None  # Formato YYYYMMDD, ex: "20241203"
    buscar_detalhes: bool = False  # True = busca itens/docs/histórico por registro (muito mais lento)
    tamanho_pagina: Optional[int] = None  # None = usa DEFAULT_PAGE_SIZE (500). Use 10 para testes rápidos.
    
    class Config:
        json_schema_extra = {
            "example": {
                "dias_atras": 1,
                "modalidades": [6],
                "uf": None,
                "limite_paginas": 1,
                "data_referencia": None,
                "buscar_detalhes": False,
                "tamanho_pagina": 10
            }
        }

class ClassificarRequest(BaseModel):
    """Modelo para requisição de classificação manual"""
    limite: int = 25000
    paralelo: int = 5  # chamadas simultâneas à Mistral

    class Config:
        json_schema_extra = {
            "example": {
                "limite": 25000,
                "paralelo": 5
            }
        }

class ConfigClassificacaoScheduler(BaseModel):
    """Modelo para configurar o scheduler de classificação"""
    ativo: bool = True
    horario: str = "17:00"         # HH:MM UTC
    lote_maximo: int = 9000        # licitações por execução diária
    paralelo: int = 5              # chamadas simultâneas à Mistral

    class Config:
        json_schema_extra = {
            "example": {
                "ativo": True,
                "horario": "17:00",
                "lote_maximo": 9000,
                "paralelo": 5
            }
        }

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title=ServerConfig.APP_NAME,
    description=ServerConfig.DESCRIPTION,
    version=ServerConfig.VERSION
)

scheduler = BackgroundScheduler()
scheduler_config = {
    "ativo": False,
    "horario": SchedulerConfig.HORARIO_PADRAO,
    "modalidades": list(SchedulerConfig.MODALIDADES_PADRAO),  # todas: 1-13
    "dias_atras": SchedulerConfig.DIAS_ATRAS,
    "limite_paginas": None  # None = SEM LIMITE (busca tudo!)
}

# Configuração em memória do scheduler de classificação
scheduler_classificacao_config = {
    "ativo": True,
    "horario": ClassificacaoSchedulerConfig.HORARIO,
    "lote_maximo": ClassificacaoSchedulerConfig.LOTE_MAXIMO,
    "paralelo": ClassificacaoSchedulerConfig.PARALELO,
}

# ============================================================================
# FUNÇÕES DE PERSISTÊNCIA DE CONFIGURAÇÃO
# ============================================================================

def carregar_config_scheduler_do_banco() -> dict:
    """Carrega configuração do scheduler do Supabase (tabela scheduler_horario)"""
    
    if not SUPABASE_ENABLED:
        logger.warning("⚠️ Supabase não conectado - usando configuração padrão")
        return scheduler_config
    
    try:
        # Busca a configuração (sempre id=1)
        resultado = supabase.table('scheduler_horario')\
            .select('*')\
            .eq('id', 1)\
            .execute()
        
        if resultado.data and len(resultado.data) > 0:
            config_db = resultado.data[0]
            
            # Extrai hora e minuto do campo time
            hora_execucao = config_db.get('hora_execucao', '06:00:00')
            if isinstance(hora_execucao, str):
                # Remove segundos se houver (06:00:00 -> 06:00)
                horario = hora_execucao.split(':')[0] + ':' + hora_execucao.split(':')[1]
            else:
                horario = '06:00'
            
            config = {
                "id": config_db.get('id'),
                "ativo": config_db.get('ativo', False),
                "horario": horario,
                "modalidades": scheduler_config.get('modalidades', SchedulerConfig.MODALIDADES_PADRAO),
                "dias_atras": config_db.get('dias_retroativos', 1),
                "limite_paginas": scheduler_config.get('limite_paginas', 50)  # Usa do config padrão
            }
            
            logger.info(f"✅ Configuração carregada do banco: {config['horario']}, dias_retroativos={config['dias_atras']}")
            return config
        else:
            logger.info("💡 Nenhuma configuração no banco - criando registro padrão")
            # Cria registro inicial
            supabase.table('scheduler_horario').insert({
                'id': 1,
                'hora_execucao': '06:00:00',
                'ativo': False,
                'dias_retroativos': 1
            }).execute()
            return scheduler_config
            
    except Exception as e:
        logger.error(f"❌ Erro ao carregar configuração do banco: {str(e)}")
        logger.info("💡 Usando configuração padrão")
        return scheduler_config

def salvar_config_scheduler_no_banco(config: dict) -> bool:
    """Salva configuração do scheduler no Supabase (tabela scheduler_horario)"""
    
    if not SUPABASE_ENABLED:
        logger.warning("⚠️ Supabase não conectado - configuração não será persistida")
        return False
    
    try:
        from datetime import datetime, timedelta
        
        # Converte horario HH:MM para HH:MM:SS
        horario = config.get('horario', '06:00')
        hora_execucao = horario + ':00' if len(horario.split(':')) == 2 else horario
        
        # Calcula próxima execução se estiver ativo
        proxima_execucao = None
        if config.get('ativo'):
            hora, minuto = horario.split(':')
            agora = datetime.now()
            proxima = agora.replace(hour=int(hora), minute=int(minuto), second=0, microsecond=0)
            
            # Se já passou hoje, agenda para amanhã
            if proxima <= agora:
                proxima = proxima + timedelta(days=1)
            
            proxima_execucao = proxima.isoformat()
        
        dados = {
            "hora_execucao": hora_execucao,
            "ativo": config.get('ativo', False),
            "dias_retroativos": config.get('dias_atras', 1),
            "proxima_execucao": proxima_execucao,
            "updated_at": datetime.now().isoformat()
        }
        
        # Atualiza registro (sempre id=1)
        resultado = supabase.table('scheduler_horario')\
            .update(dados)\
            .eq('id', 1)\
            .execute()
        
        if resultado.data:
            logger.info(f"✅ Configuração salva no banco: {horario}, ativo={config.get('ativo')}, dias_retroativos={config.get('dias_atras')}")
            if proxima_execucao:
                logger.info(f"📅 Próxima execução: {proxima.strftime('%d/%m/%Y %H:%M')}")
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"❌ Erro ao salvar configuração no banco: {str(e)}")
        return False

# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def extrair_partes_numero_controle(numero_controle: str) -> tuple:
    """Extrai CNPJ, ano e sequencial do número de controle PNCP"""
    try:
        partes = numero_controle.split('-')
        cnpj = partes[0]
        resto = partes[2].split('/')
        sequencial = resto[0]
        ano = resto[1]
        return cnpj, ano, sequencial
    except:
        return None, None, None

def buscar_contratacoes_pncp(data_inicial: str, data_final: str, 
                              modalidade: int, uf: Optional[str] = None,
                              pagina: int = 1,
                              page_size: Optional[int] = None) -> dict:
    """Busca contratações na API de consulta do PNCP com retry automático"""
    import time

    endpoint = f"{PNCPConfig.CONSULTA_URL}/v1/contratacoes/publicacao"
    
    tamanho = page_size if page_size and 1 <= page_size <= PNCPConfig.MAX_PAGE_SIZE else PNCPConfig.DEFAULT_PAGE_SIZE

    params = {
        'dataInicial': data_inicial,
        'dataFinal': data_final,
        'codigoModalidadeContratacao': modalidade,
        'pagina': pagina,
        'tamanhoPagina': tamanho,
    }
    
    # Só adiciona UF se for válido (não vazio, não "string", não None)
    if uf and uf.strip() and uf.lower() != "string" and len(uf) == 2:
        params['uf'] = uf.upper()
    
    max_tentativas = 3
    for tentativa in range(1, max_tentativas + 1):
        try:
            response = requests.get(
                endpoint, 
                headers=PNCPConfig.HEADERS, 
                params=params, 
                timeout=PNCPConfig.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"⏱️ Timeout na página {pagina} modalidade {modalidade} (tentativa {tentativa}/{max_tentativas})")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            logger.warning(f"❌ HTTP {status} na página {pagina} modalidade {modalidade} (tentativa {tentativa}/{max_tentativas}): {e}")
            # 404 ou 400 em geral significa que não há mais páginas — não retenta
            if e.response is not None and e.response.status_code in (400, 404):
                logger.info(f"ℹ️ API retornou {status} para página {pagina} — sem mais registros nesse intervalo")
                return {"data": [], "totalRegistros": 0, "totalPaginas": 0}
        except Exception as e:
            logger.warning(f"⚠️ Erro inesperado na página {pagina} modalidade {modalidade} (tentativa {tentativa}/{max_tentativas}): {type(e).__name__}: {e}")
        
        if tentativa < max_tentativas:
            espera = 2 ** tentativa  # backoff: 2s, 4s
            logger.info(f"🔄 Aguardando {espera}s antes de retentar...")
            time.sleep(espera)
    
    logger.error(f"🚫 Página {pagina} modalidade {modalidade} falhou após {max_tentativas} tentativas — pulando")
    return {"data": [], "totalRegistros": 0, "totalPaginas": 0}

def buscar_detalhes_completos(cnpj: str, ano: str, sequencial: str) -> dict:
    """Busca itens, documentos e histórico de uma contratação"""
    
    detalhes = {
        "itens": [],
        "documentos": [],
        "historico": []
    }
    
    # Buscar Itens
    try:
        url_itens = f"{PNCPConfig.INTEGRACAO_URL}/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
        response = requests.get(
            url_itens, 
            headers=PNCPConfig.HEADERS, 
            timeout=PNCPConfig.REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            detalhes["itens"] = response.json()
    except Exception as e:
        logger.warning(f"Erro ao buscar itens: {str(e)}")
    
    # Buscar Documentos
    try:
        url_docs = f"{PNCPConfig.INTEGRACAO_URL}/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
        response = requests.get(
            url_docs, 
            headers=PNCPConfig.HEADERS, 
            timeout=PNCPConfig.REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            detalhes["documentos"] = response.json()
    except Exception as e:
        logger.warning(f"Erro ao buscar documentos: {str(e)}")
    
    # Buscar Histórico
    try:
        url_hist = f"{PNCPConfig.INTEGRACAO_URL}/orgaos/{cnpj}/compras/{ano}/{sequencial}/historico"
        response = requests.get(
            url_hist, 
            headers=PNCPConfig.HEADERS, 
            timeout=PNCPConfig.REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            detalhes["historico"] = response.json()
    except Exception as e:
        logger.warning(f"Erro ao buscar histórico: {str(e)}")
    
    return detalhes

def mapear_para_supabase(contratacao: dict, detalhes: dict) -> dict:
    """Mapeia dados da API PNCP para o formato da tabela Supabase"""
    
    # Calcula valor total dos itens se disponível
    valor_total = contratacao.get('valorTotalEstimado', 0)
    if not valor_total and detalhes['itens']:
        valor_total = sum(item.get('valorTotal', 0) for item in detalhes['itens'])
    
    # Extrai partes do número de controle para construir link do portal
    numero_controle = contratacao.get('numeroControlePNCP')
    link_portal = None
    
    if numero_controle:
        try:
            cnpj, ano, sequencial = extrair_partes_numero_controle(numero_controle)
            if cnpj and ano and sequencial:
                # Monta URL da página do portal
                link_portal = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}"
        except:
            pass
    
    # Prepara dados_completos com TODAS as informações da API
    # IMPORTANTE: Fazemos uma cópia profunda para preservar TODOS os campos da API
    # Isso garante que nenhum campo seja perdido, incluindo campos que podem não estar
    # documentados mas que a API retorna (ex: existeResultado, fontesOrcamentarias, etc)
    dados_completos = copy.deepcopy(contratacao)
    
    # Adiciona link do portal (não sobrescreve se já existir)
    if link_portal:
        dados_completos['link_portal_pncp'] = link_portal
    
    # Garante que as datas de abertura e encerramento estejam no formato correto
    # A API pode retornar em diferentes formatos, normalizamos para ISO 8601
    data_abertura = contratacao.get('dataAberturaProposta')
    data_encerramento = contratacao.get('dataEncerramentoProposta')
    
    # Se as datas vierem como string, mantém; se vierem como objeto datetime, converte
    if data_abertura:
        if isinstance(data_abertura, str):
            dados_completos['dataAberturaProposta'] = data_abertura
        else:
            # Se for datetime ou outro formato, converte para ISO string
            dados_completos['dataAberturaProposta'] = str(data_abertura)
    else:
        # Garante que o campo exista mesmo se for None
        dados_completos['dataAberturaProposta'] = None
    
    if data_encerramento:
        if isinstance(data_encerramento, str):
            dados_completos['dataEncerramentoProposta'] = data_encerramento
        else:
            dados_completos['dataEncerramentoProposta'] = str(data_encerramento)
    else:
        # Garante que o campo exista mesmo se for None
        dados_completos['dataEncerramentoProposta'] = None
    
    # NOTA: Não precisamos adicionar campos explicitamente porque já copiamos
    # TODOS os campos da API com copy.deepcopy(). Isso garante que campos como:
    # - existeResultado
    # - dataInclusao, dataAtualizacao, dataAtualizacaoGlobal
    # - fontesOrcamentarias
    # - orcamentoSigilosoCodigo, orcamentoSigilosoDescricao
    # - tipoInstrumentoConvocatorioCodigo
    # - linkProcessoEletronico
    # E todos os outros campos retornados pela API sejam preservados automaticamente
    
    # Adiciona link do portal em cada documento também
    documentos_com_links = detalhes.get('documentos', []).copy()
    for doc in documentos_com_links:
        doc['link_portal_edital'] = link_portal
    
    return {
        "numero_controle_pncp": numero_controle,
        "id_pncp": numero_controle,  # Mesmo valor
        "objeto_compra": contratacao.get('objetoCompra'),
        "valor_total_estimado": float(valor_total) if valor_total else None,
        "data_publicacao_pncp": contratacao.get('dataPublicacaoPncp'),
        "orgao_razao_social": contratacao.get('orgaoEntidade', {}).get('razaoSocial') or contratacao.get('orgaoEntidade', {}).get('razaosocial'),
        "uf_sigla": contratacao.get('unidadeOrgao', {}).get('ufSigla'),
        "modalidade_nome": contratacao.get('modalidadeNome'),
        "link_portal_pncp": link_portal,  # ⭐ Link do portal em coluna dedicada
        "dados_completos": dados_completos,  # JSON completo com TODAS as informações
        "itens": detalhes.get('itens', []),
        "anexos": documentos_com_links,  # Documentos com link do portal
        "historico": detalhes.get('historico', []),
        "data_atualizacao": datetime.now().isoformat()
    }

def salvar_no_supabase(dados: dict) -> bool:
    """Salva ou atualiza licitação no Supabase (evita duplicatas e preserva dados existentes)"""
    
    numero_controle = dados.get('numero_controle_pncp', 'N/A')
    link = dados.get('link_portal_pncp') or ''
    objeto = (dados.get('objeto_compra') or '')[:80]
    orgao = dados.get('orgao_razao_social') or ''
    uf = dados.get('uf_sigla') or ''
    valor = dados.get('valor_total_estimado')
    valor_txt = f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if valor else "N/I"

    if not SUPABASE_ENABLED:
        logger.info(
            f"🔵 [TESTE] {numero_controle} | {uf} | {valor_txt}\n"
            f"         Objeto: {objeto}\n"
            f"         Órgão:  {orgao}\n"
            f"         Link:   {link or 'N/D'}"
        )
        return True
    
    try:
        # Verifica se já existe e busca dados_completos existente
        resultado = supabase.table(SupabaseConfig.TABLE_NAME)\
            .select('id, dados_completos')\
            .eq('numero_controle_pncp', numero_controle)\
            .execute()
        
        if resultado.data and len(resultado.data) > 0:
            # Atualiza registro existente - preserva dados_completos existentes
            existente = resultado.data[0]
            dados_completos_existente = existente.get('dados_completos') or {}
            
            # Faz merge dos dados_completos: preserva existentes e atualiza com novos
            if 'dados_completos' in dados:
                dados_completos_novo = dados['dados_completos'] or {}
                # Merge: dados existentes primeiro, depois novos (novos sobrescrevem)
                dados['dados_completos'] = {**dados_completos_existente, **dados_completos_novo}
            
            # Atualiza registro
            supabase.table(SupabaseConfig.TABLE_NAME)\
                .update(dados)\
                .eq('numero_controle_pncp', numero_controle)\
                .execute()
            logger.info(f"♻️  Atualizado: {numero_controle} | {uf} | {valor_txt} | {link or 'sem link'}")
        else:
            # Insere novo registro
            supabase.table(SupabaseConfig.TABLE_NAME)\
                .insert(dados)\
                .execute()
            logger.info(f"✅ Inserido:   {numero_controle} | {uf} | {valor_txt} | {link or 'sem link'}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erro ao salvar {numero_controle}: {str(e)}")
        return False

def processar_extracao(dias_atras: int = 1, modalidades: List[int] = [6, 8], 
                       uf: Optional[str] = None, limite_paginas: int = 10,
                       data_referencia: Optional[str] = None,
                       buscar_detalhes: bool = False,
                       tamanho_pagina: Optional[int] = None) -> dict:
    """Processa extração de licitações com visualização Rich"""
    
    # Tamanho de página efetivo para esta execução
    page_size = tamanho_pagina if tamanho_pagina and 1 <= tamanho_pagina <= PNCPConfig.MAX_PAGE_SIZE else PNCPConfig.DEFAULT_PAGE_SIZE

    # Calcula datas
    if data_referencia:
        try:
            data_final = datetime.strptime(data_referencia, "%Y%m%d")
            logger.info(f"Usando data_referencia especificada: {data_final.strftime('%d/%m/%Y')}")
        except:
            data_final = datetime.now()
            logger.info(f"Usando data do sistema: {data_final.strftime('%d/%m/%Y')}")
    else:
        # Usa data real do sistema (ano vigente)
        data_final = datetime.now()
        logger.info(f"Usando data do sistema: {data_final.strftime('%d/%m/%Y')} (Ano {data_final.year})")
    
    data_inicial = data_final - timedelta(days=dias_atras)
    data_inicial_str = data_inicial.strftime("%Y%m%d")
    data_final_str = data_final.strftime("%Y%m%d")
    
    # Painel de informações inicial
    sem_limite_geral = (limite_paginas == 0 or limite_paginas is None)
    limite_texto = "SEM LIMITE (busca TUDO! ♾️)" if sem_limite_geral else f"{limite_paginas} por modalidade"
    detalhes_texto = "[green]SIM[/green] (itens + docs + histórico — mais lento)" if buscar_detalhes else "[yellow]NÃO[/yellow] (apenas listagem — rápido)"
    max_registros_texto = f"~{limite_paginas * page_size}" if not sem_limite_geral else "todos"
    
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]🚀 EXTRAÇÃO DE LICITAÇÕES DO PNCP[/bold cyan]\n\n"
        f"[yellow]📅 Período:[/yellow] {data_inicial.strftime('%d/%m/%Y')} até {data_final.strftime('%d/%m/%Y')}\n"
        f"[yellow]📋 Modalidades:[/yellow] {len(modalidades)} ({', '.join(map(str, modalidades))})\n"
        f"[yellow]🗺️  UF:[/yellow] {uf if uf else 'Todos os estados'}\n"
        f"[yellow]📄 Tamanho página:[/yellow] {page_size} registros\n"
        f"[yellow]📊 Limite páginas:[/yellow] {limite_texto} (máx. {max_registros_texto} registros/modalidade)\n"
        f"[yellow]🔍 Buscar detalhes:[/yellow] {detalhes_texto}",
        border_style="cyan",
        title="⚙️ Configuração"
    ))
    console.print()
    
    estatisticas = {
        "data_inicial": data_inicial_str,
        "data_final": data_final_str,
        "total_encontrados": 0,
        "total_processados": 0,
        "total_salvos": 0,
        "total_erros": 0,
        "modalidades": {}
    }

    # Acumula registros para exibir tabela de links ao final (útil no modo teste)
    registros_extraidos: list = []
    
    # Progress bar com Rich
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("[cyan]{task.completed}/{task.total}[/cyan]"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        
        # Task principal para todas as modalidades
        task_geral = progress.add_task(
            "[cyan]Processando modalidades...", 
            total=len(modalidades)
        )
        
        # Para cada modalidade
        for idx_mod, modalidade in enumerate(modalidades, 1):
            modalidade_nome = ModalidadesConfig.get_nome(modalidade)
            
            console.print(f"\n[bold yellow]📋 Modalidade {idx_mod}/{len(modalidades)}: {modalidade_nome}[/bold yellow]")
            
            modalidade_stats = {
                "encontrados": 0,
                "processados": 0,
                "salvos": 0
            }
            
            pagina = 1
            task_modalidade = None
            sem_limite = (limite_paginas == 0 or limite_paginas is None)
            
            while True:
                # Busca contratações
                resultado = buscar_contratacoes_pncp(
                    data_inicial_str, 
                    data_final_str, 
                    modalidade, 
                    uf, 
                    pagina,
                    page_size=page_size,
                )
                
                contratacoes = resultado.get('data', [])
                total_paginas = resultado.get('totalPaginas', 0)
                # totalRegistros da API PNCP é o acumulado histórico (não o total do período).
                # Usamos totalPaginas * page_size como estimativa real do período filtrado.
                total_registros_periodo = total_paginas * page_size
                
                if not contratacoes:
                    console.print(f"   [dim]Nenhum registro na página {pagina} — encerrando modalidade[/dim]")
                    break
                
                # Cria task para esta modalidade na primeira página
                if task_modalidade is None and total_paginas > 0:
                    if sem_limite:
                        max_registros = total_registros_periodo
                    else:
                        max_registros = min(total_registros_periodo, limite_paginas * page_size)
                    
                    task_modalidade = progress.add_task(
                        f"   [green]Buscando {modalidade_nome}...",
                        total=max(max_registros, 1)
                    )
                
                paginas_limite = min(total_paginas, limite_paginas) if not sem_limite else total_paginas
                console.print(f"   [dim]Página {pagina}/{paginas_limite}: {len(contratacoes)} licitações[/dim]")
                
                modalidade_stats["encontrados"] += len(contratacoes)
            
                # Processa cada contratação
                for contratacao in contratacoes:
                    try:
                        numero_controle = contratacao.get('numeroControlePNCP')
                        
                        if not numero_controle:
                            continue
                        
                        # Extrai partes do número de controle
                        cnpj, ano, sequencial = extrair_partes_numero_controle(numero_controle)
                        
                        if not cnpj:
                            continue
                        
                        # Busca detalhes (itens/docs/histórico) somente se solicitado
                        # Desativado por padrão: cada registro requer 3 chamadas extras
                        if buscar_detalhes:
                            detalhes = buscar_detalhes_completos(cnpj, ano, sequencial)
                        else:
                            detalhes = {"itens": [], "documentos": [], "historico": []}
                        
                        # Mapeia para formato Supabase
                        dados_supabase = mapear_para_supabase(contratacao, detalhes)

                        # Acumula para tabela de links exibida ao final
                        registros_extraidos.append({
                            "numero": dados_supabase.get("numero_controle_pncp", ""),
                            "uf": dados_supabase.get("uf_sigla", ""),
                            "modalidade": dados_supabase.get("modalidade_nome", ""),
                            "objeto": (dados_supabase.get("objeto_compra") or "")[:60],
                            "valor": dados_supabase.get("valor_total_estimado"),
                            "link": dados_supabase.get("link_portal_pncp") or "",
                        })
                        
                        # Salva no Supabase
                        if salvar_no_supabase(dados_supabase):
                            modalidade_stats["salvos"] += 1
                        
                        modalidade_stats["processados"] += 1
                        
                        # Atualiza progress bar
                        if task_modalidade is not None:
                            progress.update(task_modalidade, advance=1)
                        
                    except Exception as e:
                        logger.error(f"Erro ao processar {contratacao.get('numeroControlePNCP')}: {str(e)}")
                        estatisticas["total_erros"] += 1
                
                # Próxima página
                pagina += 1
                
                # Verifica se deve continuar
                if pagina > total_paginas:
                    break
                
                # Se tem limite de páginas, verifica se atingiu
                if not sem_limite and pagina > limite_paginas:
                    console.print(f"   [yellow]⚠️ Limite de {limite_paginas} páginas atingido[/yellow]")
                    break
            
            # Completa task da modalidade se existir
            if task_modalidade is not None:
                progress.update(task_modalidade, completed=modalidade_stats["processados"])
            
            # Atualiza estatísticas
            estatisticas["modalidades"][modalidade] = modalidade_stats
            estatisticas["total_encontrados"] += modalidade_stats["encontrados"]
            estatisticas["total_processados"] += modalidade_stats["processados"]
            estatisticas["total_salvos"] += modalidade_stats["salvos"]
            
            # Mostra resumo da modalidade
            console.print(f"   [green]✓ {modalidade_stats['salvos']} salvos de {modalidade_stats['encontrados']} encontrados[/green]")
            
            # Atualiza progress geral
            progress.update(task_geral, advance=1)
    
    # Tabela de resumo final
    console.print()
    tabela = Table(title="📊 Resumo da Extração", box=box.ROUNDED, show_header=True, header_style="bold cyan")
    tabela.add_column("Modalidade", style="yellow", width=25)
    tabela.add_column("Encontrados", justify="right", style="cyan")
    tabela.add_column("Processados", justify="right", style="blue")
    tabela.add_column("Salvos", justify="right", style="green")
    tabela.add_column("Taxa", justify="right", style="magenta")
    
    for mod_codigo, stats in estatisticas["modalidades"].items():
        nome = ModalidadesConfig.get_nome(mod_codigo)
        taxa = f"{(stats['salvos']/stats['encontrados']*100):.1f}%" if stats['encontrados'] > 0 else "0%"
        tabela.add_row(
            nome,
            str(stats['encontrados']),
            str(stats['processados']),
            f"[bold green]{stats['salvos']}[/bold green]",
            taxa
        )
    
    # Linha de total
    taxa_total = f"{(estatisticas['total_salvos']/estatisticas['total_encontrados']*100):.1f}%" if estatisticas['total_encontrados'] > 0 else "0%"
    tabela.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{estatisticas['total_encontrados']}[/bold]",
        f"[bold]{estatisticas['total_processados']}[/bold]",
        f"[bold green]{estatisticas['total_salvos']}[/bold green]",
        f"[bold]{taxa_total}[/bold]",
        style="bold"
    )
    
    console.print(tabela)
    console.print()

    # Tabela de editais extraídos com links — exibe até 50 para não poluir o log
    if registros_extraidos:
        modo_label = "[yellow]MODO TESTE — não salvo no banco[/yellow]" if not SUPABASE_ENABLED else "[green]SALVO NO SUPABASE[/green]"
        exibir = registros_extraidos[:50]
        tbl_links = Table(
            title=f"🔗 Editais Extraídos ({len(registros_extraidos)} total) — {modo_label}",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold magenta",
            show_lines=False,
        )
        tbl_links.add_column("#", style="dim", width=4, justify="right")
        tbl_links.add_column("UF", width=4)
        tbl_links.add_column("Modalidade", width=22, style="yellow")
        tbl_links.add_column("Objeto", width=55, no_wrap=True)
        tbl_links.add_column("Valor Estimado", width=16, justify="right", style="cyan")
        tbl_links.add_column("Link Portal PNCP", style="blue")

        for i, r in enumerate(exibir, 1):
            valor = r.get("valor")
            if valor:
                valor_fmt = f"R$ {valor:,.0f}".replace(",", ".")
            else:
                valor_fmt = "[dim]N/I[/dim]"
            link = r.get("link") or "[dim]sem link[/dim]"
            tbl_links.add_row(
                str(i),
                r.get("uf", ""),
                r.get("modalidade", ""),
                r.get("objeto", ""),
                valor_fmt,
                link,
            )

        if len(registros_extraidos) > 50:
            tbl_links.add_row("...", "", "", f"[dim]+ {len(registros_extraidos)-50} registros omitidos[/dim]", "", "")

        console.print(tbl_links)
        console.print()

    # Painel final
    modo_aviso = "\n[bold yellow]⚠️  MODO TESTE — configure SUPABASE_URL e SUPABASE_KEY para salvar no banco![/bold yellow]" if not SUPABASE_ENABLED else ""
    console.print(Panel.fit(
        f"[bold green]✅ EXTRAÇÃO CONCLUÍDA COM SUCESSO![/bold green]\n\n"
        f"[cyan]📦 Total Encontrados:[/cyan] {estatisticas['total_encontrados']}\n"
        f"[cyan]✓ Total Salvos:[/cyan] [bold green]{estatisticas['total_salvos']}[/bold green]\n"
        f"[cyan]❌ Erros:[/cyan] {estatisticas['total_erros']}\n"
        f"[cyan]📊 Taxa de Sucesso:[/cyan] {taxa_total}"
        f"{modo_aviso}",
        border_style="green" if SUPABASE_ENABLED else "yellow",
        title="🎉 Resultado"
    ))
    console.print()
    
    return estatisticas

# ============================================================================
# TAREFA AGENDADA
# ============================================================================

def atualizar_ultima_execucao():
    """Atualiza última execução e calcula próxima no banco"""
    
    if not SUPABASE_ENABLED:
        return
    
    try:
        from datetime import datetime, timedelta
        
        agora = datetime.now()
        
        # Carrega configuração atual
        config = supabase.table('scheduler_horario')\
            .select('hora_execucao, ativo')\
            .eq('id', 1)\
            .execute()
        
        if config.data:
            hora_execucao = config.data[0].get('hora_execucao', '06:00:00')
            hora, minuto = hora_execucao.split(':')[:2]
            
            # Calcula próxima execução (sempre amanhã no mesmo horário)
            proxima = agora + timedelta(days=1)
            proxima = proxima.replace(hour=int(hora), minute=int(minuto), second=0, microsecond=0)
            
            # Atualiza no banco
            supabase.table('scheduler_horario')\
                .update({
                    'ultima_execucao': agora.isoformat(),
                    'proxima_execucao': proxima.isoformat(),
                    'updated_at': agora.isoformat()
                })\
                .eq('id', 1)\
                .execute()
            
            logger.info(f"📅 Atualizado: Última={agora.strftime('%d/%m/%Y %H:%M')}, Próxima={proxima.strftime('%d/%m/%Y %H:%M')}")
            
    except Exception as e:
        logger.error(f"❌ Erro ao atualizar última execução: {str(e)}")

def tarefa_extracao_automatica():
    """Tarefa do scheduler: SOMENTE extração PNCP. Não depende da Mistral; classificação roda em outro horário (17:00)."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    modalidades = scheduler_config.get("modalidades", SchedulerConfig.MODALIDADES_PADRAO)
    dias_atras = scheduler_config.get("dias_atras", SchedulerConfig.DIAS_ATRAS)
    limite_paginas = scheduler_config.get("limite_paginas", SchedulerConfig.LIMITE_PAGINAS_AUTO)

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]⏰ EXTRAÇÃO AUTOMÁTICA AGENDADA[/bold cyan]\n\n"
        f"[yellow]Início:[/yellow] {agora}\n"
        f"[yellow]Modalidades:[/yellow] {len(modalidades)} → {', '.join(map(str, modalidades))}\n"
        f"[yellow]Dias atrás:[/yellow] {dias_atras} | [yellow]Limite páginas:[/yellow] {limite_paginas or 'Sem limite'}",
        border_style="cyan",
        title="[Scheduler Extração]"
    ))
    console.print()
    logger.info("⏰ Executando extração automática agendada (somente PNCP)...")

    atualizar_ultima_execucao()

    try:
        resultado = processar_extracao(
            dias_atras=dias_atras,
            modalidades=modalidades,
            limite_paginas=limite_paginas,
            buscar_detalhes=SchedulerConfig.BUSCAR_DETALHES,
        )
        total_salvos = resultado.get('total_salvos', 0)
        total_encontrados = resultado.get('total_encontrados', 0)

        console.print()
        console.print(Panel.fit(
            f"[bold green]✅ EXTRAÇÃO AUTOMÁTICA CONCLUÍDA[/bold green]\n\n"
            f"[cyan]Encontrados:[/cyan] {total_encontrados}\n"
            f"[cyan]Salvos/atualizados:[/cyan] [bold]{total_salvos}[/bold]\n"
            f"[cyan]Erros:[/cyan] {resultado.get('total_erros', 0)}\n\n"
            f"[dim]Classificação por IA roda em horário separado (17:00).[/dim]",
            border_style="green",
            title="[Resultado Extração]"
        ))
        console.print()
        logger.info(f"✅ Extração automática concluída: {total_salvos} registros salvos de {total_encontrados} encontrados")

    except Exception as e:
        logger.error(f"❌ Erro na extração automática: {str(e)}")
        console.print(Panel.fit(f"[bold red]❌ Erro na extração automática[/bold red]\n\n{e}", border_style="red", title="[Erro]"))
        console.print()


def job_classificacao_diaria():
    """Job do scheduler: classificação diária usando config em memória (scheduler_classificacao_config)."""
    lote    = scheduler_classificacao_config.get("lote_maximo", ClassificacaoSchedulerConfig.LOTE_MAXIMO)
    paralelo = scheduler_classificacao_config.get("paralelo",   ClassificacaoSchedulerConfig.PARALELO)
    logger.info("🧠 Executando classificação diária agendada (lote=%d, paralelo=%d)...", lote, paralelo)
    try:
        asyncio.run(tarefa_classificacao_automatica(lote=lote, paralelo=paralelo))
    except Exception as e:
        logger.error("❌ Erro no job de classificação diária: %s", e)
        console.print(Panel.fit(f"[red]Erro: {e}[/red]", border_style="red", title="[Classificação Diária]"))

async def tarefa_classificacao_automatica(
    lote: int = ClassificacaoSchedulerConfig.LOTE_MAXIMO,
    paralelo: int = ClassificacaoSchedulerConfig.PARALELO,
):
    """Classificação automática — processa até `lote` pendentes com `paralelo` chamadas simultâneas."""
    try:
        if not SUPABASE_ENABLED:
            logger.warning("⚠️ Supabase não conectado — pulando classificação")
            console.print(Panel.fit(
                "[yellow]Supabase não conectado — classificação ignorada.[/yellow]",
                border_style="yellow", title="[Classificação]",
            ))
            return

        classificador = ClassificadorIA(supabase)

        console.print(Panel.fit(
            f"[bold magenta]CLASSIFICAÇÃO AUTOMÁTICA (Mistral)[/bold magenta]\n\n"
            f"[cyan]Lote:[/cyan]    {lote}\n"
            f"[cyan]Paralelo:[/cyan] {paralelo} simultâneas",
            border_style="magenta", title="[Início]",
        ))
        console.print()

        stats = await classificador.classificar_pendentes(limite=lote, paralelo=paralelo)
        logger.info("✅ Classificação automática concluída: %s", stats)

        console.print()
        console.print(Panel.fit(
            f"[bold green]✅ CLASSIFICAÇÃO AUTOMÁTICA CONCLUÍDA[/bold green]\n\n"
            f"[cyan]Processados:[/cyan] {stats.get('processados', 0)}\n"
            f"[cyan]Sucessos:[/cyan]    {stats.get('sucessos', 0)}\n"
            f"[cyan]Falhas:[/cyan]      {stats.get('falhas', 0)}",
            border_style="green", title="[Resultado]",
        ))
        console.print()
    except Exception as e:
        logger.error("❌ Erro na classificação automática: %s", e)
        console.print(Panel.fit(f"[bold red]Erro na classificação automática[/bold red]\n\n{e}", border_style="red", title="[Erro]"))
        console.print()

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
def health():
    """
    Health check para produção (ex.: Render).
    Retorna 200 se a API está no ar. Use healthCheckPath: /health no Render se quiser.
    """
    return {"status": "ok", "servico": "pncp-licitacoes-api"}

@app.get("/")
def root():
    """Endpoint raiz com informações da API"""
    from datetime import datetime
    
    return {
        "nome": ServerConfig.APP_NAME,
        "versao": ServerConfig.VERSION,
        "status": "online",
        "data_sistema": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "supabase": {
            "conectado": SUPABASE_ENABLED,
            "tabela": SupabaseConfig.TABLE_NAME if SUPABASE_ENABLED else "N/A"
        },
        "classificacao_ia": {
            "disponivel": SUPABASE_ENABLED and MistralConfig.is_configured(),
            "motivo_indisponivel": (
                None if (SUPABASE_ENABLED and MistralConfig.is_configured()) else
                "Supabase: configure SUPABASE_URL e SUPABASE_KEY." if not SUPABASE_ENABLED else
                "Mistral: configure MISTRAL_API_KEY no ambiente (ex.: Render → Environment)."
            ),
            "mistral_key_length": len(MistralConfig.API_KEY)
        },
        "scheduler": {
            "ativo": scheduler_config["ativo"],
            "horario": scheduler_config["horario"],
            "dias_atras": scheduler_config.get("dias_atras", SchedulerConfig.DIAS_ATRAS),
            "modalidades": scheduler_config["modalidades"],
            "limite_paginas": scheduler_config.get("limite_paginas")
        },
        "configuracoes": {
            "tamanho_pagina_padrao": PNCPConfig.DEFAULT_PAGE_SIZE,
            "timeout": PNCPConfig.REQUEST_TIMEOUT
        },
        "endpoints": {
            "health": "GET /health",
            "docs": "/docs",
            "configuracoes": "GET /config",
            "atualizar_config": "POST /config/atualizar",
            "configurar_scheduler": "POST /scheduler/configurar",
            "status_scheduler": "GET /scheduler/status",
            "extrair_manual": "POST /extrair/manual",
            "classificar_manual": "POST /classificar/manual",
            "classificar_todas": "POST /classificar/todas",
            "estatisticas": "GET /estatisticas"
        }
    }

@app.get("/scheduler/status")
def status_scheduler():
    """Retorna status dos schedulers (extração e classificação)"""
    jobs = scheduler.get_jobs()
    jobs_info = []
    for j in jobs:
        next_run = getattr(j, "next_run_time", None)
        jobs_info.append({
            "id": j.id,
            "nome": j.name,
            "proxima_execucao": str(next_run) if next_run is not None else None
        })
    return {
        "scheduler_rodando": scheduler.running,
        "configuracao_extracao": scheduler_config,
        "configuracao_classificacao": {
            "horario": ClassificacaoSchedulerConfig.HORARIO,
            "lote_maximo": ClassificacaoSchedulerConfig.LOTE_MAXIMO
        },
        "jobs": jobs_info
    }

@app.post("/scheduler/configurar")
def configurar_scheduler(config: ConfigScheduler):
    """
    Configura e ativa/desativa o scheduler automático
    
    - **horario**: Horário da extração diária (formato HH:MM, ex: "06:00", "18:30")
    - **ativo**: True para ativar, False para desativar
    - **modalidades**: Lista de códigos de modalidades
      - [6, 8] = Pregão e Dispensa
      - [1, 4, 6, 7, 8, 9] = Todas as modalidades ⭐ (recomendado)
    - **dias_atras**: Quantos dias para trás buscar (1 = dia anterior)
    - **limite_paginas**: Limite opcional (null = busca TUDO! ⭐)
      - null = SEM LIMITE - Busca TODAS as licitações! (PADRÃO) ⭐
      - 10 = Limita (apenas para teste)
    
    **Modalidades disponíveis:**
    - 1 = Leilão Eletrônico
    - 4 = Concorrência Eletrônica
    - 6 = Pregão Eletrônico ⭐
    - 7 = Pregão Presencial
    - 8 = Dispensa de Licitação ⭐
    - 9 = Inexigibilidade
    
    **Exemplo - Buscar TODAS as modalidades (RECOMENDADO):**
    ```json
    {
      "horario": "06:00",
      "ativo": true,
      "modalidades": [1, 4, 6, 7, 8, 9],
      "dias_atras": 1,
      "limite_paginas": null
    }
    ```
    
    Isso vai buscar TODAS as licitações disponíveis, sem limites! ⭐
    """
    
    global scheduler_config
    
    try:
        # Atualiza configuração
        scheduler_config["horario"] = config.horario
        scheduler_config["modalidades"] = config.modalidades
        scheduler_config["ativo"] = config.ativo
        scheduler_config["dias_atras"] = config.dias_atras
        scheduler_config["limite_paginas"] = config.limite_paginas
        
        # Remove jobs anteriores
        scheduler.remove_all_jobs()
        
        if config.ativo:
            # Extrai hora e minuto
            hora, minuto = config.horario.split(':')
            
            # Adiciona novo job
            scheduler.add_job(
                tarefa_extracao_automatica,
                trigger=CronTrigger(hour=int(hora), minute=int(minuto)),
                id='extracao_diaria',
                name='Extração Diária PNCP',
                replace_existing=True
            )
            
            if not scheduler.running:
                scheduler.start()
            
            # Salva configuração no banco
            salvo_no_banco = salvar_config_scheduler_no_banco(scheduler_config)
            
            logger.info(f"✅ Scheduler configurado: {config.horario}")
            
            return {
                "sucesso": True,
                "mensagem": f"Scheduler ativado para executar às {config.horario}",
                "configuracao": scheduler_config,
                "persistido_no_banco": salvo_no_banco
            }
        else:
            if scheduler.running:
                scheduler.shutdown(wait=False)
            
            # Salva configuração no banco (desativado)
            salvo_no_banco = salvar_config_scheduler_no_banco(scheduler_config)
            
            logger.info("⏸️ Scheduler desativado")
            
            return {
                "sucesso": True,
                "mensagem": "Scheduler desativado",
                "configuracao": scheduler_config,
                "persistido_no_banco": salvo_no_banco
            }
            
    except Exception as e:
        logger.error(f"Erro ao configurar scheduler: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/extrair/manual")
def extrair_manual(request: ExtrairManualRequest, background_tasks: BackgroundTasks):
    """
    Extrai licitações manualmente
    
    - **dias_atras**: Quantos dias para trás buscar (1 = ontem, 7 = última semana)
    - **modalidades**: Lista de modalidades ou null para TODAS
      - null/None = Busca TODAS as modalidades ⭐
      - [6, 8] = Apenas Pregão e Dispensa
      - [1, 4, 6, 7, 8, 9] = Personalizado
    - **uf**: Sigla do estado (opcional, ex: "SP", "RJ", "DF") - deixe null para todos
    - **limite_paginas**: Limite de páginas (OPCIONAL - para testes)
      - null = SEM LIMITE - Busca TODAS as licitações disponíveis! ⭐ (PADRÃO)
      - 5 = Limita a 5 páginas por modalidade (250 licitações) - APENAS PARA TESTE
      - 10 = Limita a 10 páginas (500 licitações) - APENAS PARA TESTE
    
    ⚠️ **IMPORTANTE:** Por padrão (null), busca TODAS as licitações disponíveis!
    Só use limite para testes rápidos!
    
    **Modalidades disponíveis:**
    - 1 = Leilão Eletrônico
    - 4 = Concorrência Eletrônica
    - 6 = Pregão Eletrônico ⭐
    - 7 = Pregão Presencial
    - 8 = Dispensa de Licitação ⭐
    - 9 = Inexigibilidade
    
    **Exemplos:**
    
    Buscar TODAS as modalidades dos últimos 2 dias:
    ```json
    {
      "dias_atras": 2,
      "modalidades": null,
      "uf": null,
      "limite_paginas": 3
    }
    ```
    
    Buscar apenas Pregão Eletrônico de SP:
    ```json
    {
      "dias_atras": 1,
      "modalidades": [6],
      "uf": "SP",
      "limite_paginas": 5,
      "data_referencia": "20241203"
    }
    ```
    
    ⚠️ **ATENÇÃO - DATA DE PUBLICAÇÃO:**
    A API busca por DATA em que a licitação foi PUBLICADA no PNCP.
    
    Se seu sistema está em 2025 mas quer licitações de 2024, use data_referencia:
    ```json
    {
      "dias_atras": 30,
      "modalidades": null,
      "data_referencia": "20241203"
    }
    ```
    Isso busca licitações PUBLICADAS em novembro/dezembro de 2024.
    """
    
    logger.info(f"📥 Extração manual solicitada: {request.dias_atras} dias")
    
    # Se modalidades for None, busca TODAS
    modalidades = request.modalidades
    if modalidades is None:
        modalidades = [1, 4, 6, 7, 8, 9]  # Todas as modalidades principais
        logger.info(f"🔍 Buscando TODAS as modalidades: {modalidades}")
    
    # Validar e limpar UF
    uf_limpo = None
    if request.uf and request.uf.strip() and request.uf.lower() != "string":
        uf_limpo = request.uf.upper()
    
    # Limite de páginas (None = sem limite)
    limite = request.limite_paginas
    if limite is None:
        logger.info(f"♾️ SEM LIMITE - Buscando TODAS as licitações disponíveis!")
    else:
        logger.info(f"⚠️ LIMITE: {limite} páginas por modalidade (teste)")
    
    try:
        # Executa extração
        resultado = processar_extracao(
            dias_atras=request.dias_atras,
            modalidades=modalidades,
            uf=uf_limpo,
            limite_paginas=limite,
            data_referencia=request.data_referencia,
            buscar_detalhes=request.buscar_detalhes,
            tamanho_pagina=request.tamanho_pagina,
        )
        
        return {
            "sucesso": True,
            "mensagem": f"Extração concluída com sucesso",
            "estatisticas": resultado
        }
        
    except Exception as e:
        logger.error(f"Erro na extração manual: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def _verificar_config_classificacao():
    """Retorna (ok, detail) para uso nos endpoints de classificação."""
    if not SUPABASE_ENABLED:
        return False, "Supabase não conectado. Configure SUPABASE_URL e SUPABASE_KEY no ambiente (ex.: Render → Environment)."
    if not MistralConfig.is_configured():
        return False, "Mistral AI não configurada. Configure MISTRAL_API_KEY no ambiente (ex.: Render → Environment)."
    return True, None

@app.post("/classificar/manual", tags=["Classificação"])
async def classificar_manual(request: ClassificarRequest, background_tasks: BackgroundTasks):
    """
    Classifica licitações manualmente usando IA (Mistral). Processa em background.

    - **limite**: quantas licitações processar (padrão 25.000 — ideal para zerar o estoque inicial)
    - **paralelo**: chamadas simultâneas à Mistral (padrão 5)

    **Para zerar o estoque de 175k em lotes de 25k:**
    ```json
    { "limite": 25000, "paralelo": 5 }
    ```
    Chame este endpoint repetidamente até que `/estatisticas` mostre `pendentes_classificacao = 0`.
    """
    ok, detail = _verificar_config_classificacao()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)

    async def processar_background(limite: int, paralelo: int):
        classificador = ClassificadorIA(supabase)
        await classificador.classificar_pendentes(limite=limite, paralelo=paralelo)

    background_tasks.add_task(processar_background, request.limite, request.paralelo)

    return {
        "sucesso": True,
        "mensagem": f"Classificação iniciada em background",
        "configuracao": {
            "limite": request.limite,
            "paralelo": request.paralelo,
            "tempo_estimado": f"~{max(1, request.limite // request.paralelo)} seg",
        }
    }

@app.post("/classificar/todas", tags=["Classificação"])
async def classificar_todas(background_tasks: BackgroundTasks, paralelo: int = 5):
    """
    Classifica TODAS as licitações pendentes de uma vez (sem limite de lote).
    Processa em background. Use com cuidado — pode demorar horas para bases grandes.

    - **paralelo**: chamadas simultâneas à Mistral (padrão 5)
    """
    ok, detail = _verificar_config_classificacao()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)

    async def processar_todas():
        classificador = ClassificadorIA(supabase)

        response = supabase.table(SupabaseConfig.TABLE_NAME) \
            .select("id", count="exact") \
            .is_("subsetor_principal_id", "null") \
            .execute()

        total_pendentes = response.count if hasattr(response, "count") else 0

        if total_pendentes == 0:
            logger.info("🎉 Nenhuma licitação pendente de classificação")
            return

        logger.info(f"🧠 Iniciando classificação de {total_pendentes} licitações pendentes...")
        stats = await classificador.classificar_pendentes(limite=total_pendentes, paralelo=paralelo)
        logger.info(f"✅ Classificação concluída: {stats}")

    background_tasks.add_task(processar_todas)

    return {
        "sucesso": True,
        "mensagem": "Classificação de TODAS as licitações pendentes iniciada em background",
        "paralelo": paralelo,
    }

@app.post("/scheduler/classificacao/configurar", tags=["Classificação"])
def configurar_scheduler_classificacao(config: ConfigClassificacaoScheduler):
    """
    Ativa, desativa ou reconfigura o scheduler diário de classificação.

    - **ativo**: True = ativa às `horario` todo dia | False = desativa
    - **horario**: HH:MM UTC (padrão 17:00)
    - **lote_maximo**: licitações classificadas por execução (padrão 9.000)
    - **paralelo**: chamadas simultâneas à Mistral (padrão 5)

    **Para manutenção diária (após zerar estoque):**
    ```json
    { "ativo": true, "horario": "17:00", "lote_maximo": 9000, "paralelo": 5 }
    ```

    **Para desativar temporariamente:**
    ```json
    { "ativo": false, "horario": "17:00", "lote_maximo": 9000, "paralelo": 5 }
    ```
    """
    global scheduler_classificacao_config

    scheduler_classificacao_config.update({
        "ativo":       config.ativo,
        "horario":     config.horario,
        "lote_maximo": config.lote_maximo,
        "paralelo":    config.paralelo,
    })

    # Remove job existente
    if scheduler.get_job("classificacao_diaria"):
        scheduler.remove_job("classificacao_diaria")

    if config.ativo:
        try:
            h, m = config.horario.split(":")
            scheduler.add_job(
                job_classificacao_diaria,
                trigger=CronTrigger(hour=int(h), minute=int(m)),
                id="classificacao_diaria",
                name=f"Classificação Diária (lote {config.lote_maximo})",
                replace_existing=True,
            )
            if not scheduler.running:
                scheduler.start()
            logger.info(
                "🧠 Scheduler classificação reconfigurado: %s | lote=%d | paralelo=%d",
                config.horario, config.lote_maximo, config.paralelo,
            )
            return {
                "sucesso": True,
                "mensagem": f"Scheduler de classificação ativado para {config.horario} UTC",
                "configuracao": scheduler_classificacao_config,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        logger.info("⏸️ Scheduler de classificação desativado")
        return {
            "sucesso": True,
            "mensagem": "Scheduler de classificação desativado",
            "configuracao": scheduler_classificacao_config,
        }

@app.get("/config")
def ver_configuracoes():
    """
    Retorna todas as configurações atuais da aplicação
    """
    from datetime import datetime
    
    return {
        "data_sistema": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "supabase": {
            "configurado": SUPABASE_ENABLED,
            "url": SupabaseConfig.URL if SUPABASE_ENABLED else "Não configurado",
            "tabela": SupabaseConfig.TABLE_NAME
        },
        "pncp": {
            "url_consulta": PNCPConfig.CONSULTA_URL,
            "url_integracao": PNCPConfig.INTEGRACAO_URL,
            "tamanho_pagina_padrao": PNCPConfig.DEFAULT_PAGE_SIZE,
            "tamanho_pagina_maximo": PNCPConfig.MAX_PAGE_SIZE,
            "timeout": PNCPConfig.REQUEST_TIMEOUT
        },
        "scheduler": {
            "ativo": scheduler_config["ativo"],
            "horario": scheduler_config["horario"],
            "modalidades": scheduler_config["modalidades"],
            "dias_atras": scheduler_config.get("dias_atras", SchedulerConfig.DIAS_ATRAS),
            "limite_paginas": scheduler_config.get("limite_paginas", SchedulerConfig.LIMITE_PAGINAS_AUTO)
        },
        "servidor": {
            "host": ServerConfig.HOST,
            "porta": ServerConfig.PORT,
            "debug": ServerConfig.DEBUG
        },
        "modalidades_disponiveis": ModalidadesConfig.get_todas()
    }

@app.post("/config/atualizar")
def atualizar_configuracoes(config: ConfigGeral):
    """
    Atualiza configurações gerais da aplicação
    
    **Atenção:** Algumas mudanças só terão efeito após reiniciar a API
    
    - **tamanho_pagina**: Quantidade de registros por página (1-500)
    - **timeout_requisicao**: Timeout em segundos para requisições à API PNCP
    - **log_level**: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    try:
        # Valida tamanho de página
        if config.tamanho_pagina < 1 or config.tamanho_pagina > 500:
            raise HTTPException(
                status_code=400, 
                detail="tamanho_pagina deve estar entre 1 e 500"
            )
        
        # Valida timeout
        if config.timeout_requisicao < 5 or config.timeout_requisicao > 300:
            raise HTTPException(
                status_code=400,
                detail="timeout_requisicao deve estar entre 5 e 300 segundos"
            )
        
        # Atualiza configurações
        PNCPConfig.DEFAULT_PAGE_SIZE = config.tamanho_pagina
        PNCPConfig.REQUEST_TIMEOUT = config.timeout_requisicao
        
        # Atualiza log level
        nivel_log = getattr(logging, config.log_level.upper(), logging.INFO)
        logging.getLogger().setLevel(nivel_log)
        
        logger.info(f"✅ Configurações atualizadas: página={config.tamanho_pagina}, timeout={config.timeout_requisicao}s, log={config.log_level}")
        
        return {
            "sucesso": True,
            "mensagem": "Configurações atualizadas com sucesso",
            "configuracoes": {
                "tamanho_pagina": PNCPConfig.DEFAULT_PAGE_SIZE,
                "timeout_requisicao": PNCPConfig.REQUEST_TIMEOUT,
                "log_level": config.log_level
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao atualizar configurações: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/estatisticas")
def estatisticas(ultimos_dias: int = 30):
    """
    Retorna estatísticas da base de dados usando funções SQL agregadas.

    Pré-requisito: execute o arquivo supabase_stats_functions.sql no
    Supabase SQL Editor antes de usar este endpoint.

    - **ultimos_dias**: janela para o gráfico de volume diário (padrão 30)
    """

    if not SUPABASE_ENABLED:
        return {
            "aviso": "Supabase não configurado",
            "total_licitacoes": 0,
            "pendentes_classificacao": 0,
            "classificadas": 0,
            "por_modalidade": [],
            "por_uf": [],
            "por_dia": [],
        }

    try:
        geral        = supabase.rpc("get_stats_geral").execute()
        modalidades  = supabase.rpc("get_stats_modalidade").execute()
        ufs          = supabase.rpc("get_stats_uf").execute()
        por_dia      = supabase.rpc("get_stats_por_dia", {"ultimos_dias": ultimos_dias}).execute()

        resumo = geral.data[0] if geral.data else {}

        return {
            "total_licitacoes":          resumo.get("total_licitacoes", 0),
            "pendentes_classificacao":   resumo.get("pendentes_classificacao", 0),
            "classificadas":             resumo.get("classificadas", 0),
            "por_modalidade":            modalidades.data or [],
            "por_uf":                    ufs.data or [],
            "por_dia":                   por_dia.data or [],
        }

    except Exception as e:
        logger.error(f"Erro ao buscar estatísticas: {str(e)}")
        return {
            "erro": str(e),
            "dica": "Execute supabase_stats_functions.sql no Supabase SQL Editor para criar as funções necessárias.",
        }

@app.get("/health/db")
def health_db():
    if not SUPABASE_ENABLED:
        return {"conectado": False, "motivo": "supabase_nao_configurado"}
    try:
        resp = supabase.table(SupabaseConfig.TABLE_NAME).select('id', count='exact').limit(1).execute()
        qtd = resp.count if hasattr(resp, 'count') else None
        return {"conectado": True, "tabela": SupabaseConfig.TABLE_NAME, "ok": True, "quantidade": qtd}
    except Exception as e:
        return {"conectado": True, "ok": False, "erro": str(e)}

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

@app.on_event("startup")
def startup_event():
    """Executado ao iniciar a aplicação"""
    global scheduler_config
    
    logger.info("🚀 Iniciando PNCP Licitações API...")
    exibir_configuracoes()
    
    if SUPABASE_ENABLED:
        logger.info("✅ API pronta para uso com Supabase!")
        
        # 1) Scheduler de EXTRAÇÃO (horário do banco)
        logger.info("📥 Carregando configuração do scheduler do banco...")
        config_banco = carregar_config_scheduler_do_banco()
        
        if config_banco.get('id'):
            scheduler_config.update(config_banco)
            if config_banco.get('ativo'):
                try:
                    partes = config_banco.get('horario', '06:00').strip().split(':')
                    hora = int(partes[0]) if partes else 6
                    minuto = int(partes[1]) if len(partes) > 1 else 0
                    scheduler.add_job(
                        tarefa_extracao_automatica,
                        trigger=CronTrigger(hour=hora, minute=minuto),
                        id='extracao_diaria',
                        name='Extração Diária PNCP',
                        replace_existing=True
                    )
                    if not scheduler.running:
                        scheduler.start()
                    logger.info(f"⏰ Scheduler EXTRAÇÃO ativado: {config_banco['horario']}")
                    logger.info(f"📋 Modalidades: {config_banco['modalidades']}, Dias atrás: {config_banco['dias_atras']}")
                except Exception as e:
                    logger.error(f"❌ Erro ao ativar scheduler de extração: {str(e)}")
        
        # 2) Scheduler de CLASSIFICAÇÃO (17:00, lote 1000) - independente da Mistral no momento da extração
        try:
            partes_class = ClassificacaoSchedulerConfig.HORARIO.strip().split(':')
            h_class = int(partes_class[0]) if partes_class else 17
            m_class = int(partes_class[1]) if len(partes_class) > 1 else 0
            scheduler.add_job(
                job_classificacao_diaria,
                trigger=CronTrigger(hour=h_class, minute=m_class),
                id='classificacao_diaria',
                name='Classificação Diária (lote 1000)',
                replace_existing=True
            )
            if not scheduler.running:
                scheduler.start()
            logger.info(f"🧠 Scheduler CLASSIFICAÇÃO ativado: {ClassificacaoSchedulerConfig.HORARIO} (lote {ClassificacaoSchedulerConfig.LOTE_MAXIMO})")
        except Exception as e:
            logger.error(f"❌ Erro ao ativar scheduler de classificação: {str(e)}")
    else:
        logger.warning("⚠️ API rodando em MODO TESTE (sem Supabase)")
        logger.warning("⚠️ Configurações do scheduler NÃO serão persistidas")

@app.on_event("shutdown")
def shutdown_event():
    """Executado ao desligar a aplicação"""
    logger.info("🛑 Desligando API...")
    if scheduler.running:
        scheduler.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

