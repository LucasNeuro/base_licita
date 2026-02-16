"""
Configurações da aplicação PNCP Licitações
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# ============================================================================
# CONFIGURAÇÕES DO SUPABASE
# ============================================================================

class SupabaseConfig:
    """Configurações do Supabase"""
    
    # URL do projeto Supabase
    URL = os.getenv("SUPABASE_URL", "")
    
    # Chave de API (service_role key)
    KEY = os.getenv("SUPABASE_KEY", "")
    
    # Nome da tabela de licitações
    TABLE_NAME = "licitacoes"
    
    @classmethod
    def is_configured(cls) -> bool:
        """Verifica se o Supabase está configurado"""
        return bool(cls.URL and cls.KEY and 
                   cls.URL != "" and cls.KEY != "" and
                   "seu-projeto" not in cls.URL.lower())
    
    @classmethod
    def get_credentials(cls) -> dict:
        """Retorna credenciais como dicionário"""
        return {
            "url": cls.URL,
            "key": cls.KEY,
            "table": cls.TABLE_NAME
        }

# ============================================================================
# CONFIGURAÇÕES DA API PNCP
# ============================================================================

class PNCPConfig:
    """Configurações da API do PNCP"""
    
    # URL base da API de consulta
    CONSULTA_URL = "https://pncp.gov.br/api/consulta"
    
    # URL base da API de integração
    INTEGRACAO_URL = "https://pncp.gov.br/pncp-api/v1"
    
    # Timeout padrão para requisições (segundos)
    REQUEST_TIMEOUT = 30
    
    # Headers padrão
    HEADERS = {
        'accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # Tamanho máximo de página (conforme API)
    MAX_PAGE_SIZE = 500
    
    # Tamanho padrão de página (recomendado pela API)
    DEFAULT_PAGE_SIZE = 50

# ============================================================================
# CONFIGURAÇÕES DO SCHEDULER
# ============================================================================

class MistralConfig:
    """Configurações da Mistral AI"""
    
    # Chave de API
    API_KEY = os.getenv("MISTRAL_API_KEY", "")
    
    # Modelo a ser utilizado
    MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    
    # Temperatura (criatividade vs determinismo)
    TEMPERATURE = 0.2
    
    @classmethod
    def is_configured(cls) -> bool:
        """Verifica se a Mistral está configurada"""
        return bool(cls.API_KEY and cls.API_KEY != "")

class SchedulerConfig:
    """Configurações do agendador automático"""
    
    # Horário padrão para extração diária (formato HH:MM)
    HORARIO_PADRAO = os.getenv("HORARIO_EXTRACAO", "06:00")
    
    # Modalidades padrão para extração automática
    # 6 = Pregão Eletrônico, 8 = Dispensa de Licitação
    MODALIDADES_PADRAO = [6, 8]
    
    # Quantidade de dias para trás na extração automática
    DIAS_ATRAS = 1
    
    # Limite de páginas na extração automática
    LIMITE_PAGINAS_AUTO = 50
    
    # Limite de páginas na extração manual (padrão)
    LIMITE_PAGINAS_MANUAL = 10

# ============================================================================
# CONFIGURAÇÕES DO SERVIDOR
# ============================================================================

class ServerConfig:
    """Configurações do servidor FastAPI"""
    
    # Host
    HOST = os.getenv("HOST", "0.0.0.0")
    
    # Porta
    PORT = int(os.getenv("PORT", "8000"))
    
    # Modo de desenvolvimento (auto-reload)
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"
    
    # Nome da aplicação
    APP_NAME = "PNCP Licitações API"
    
    # Versão
    VERSION = "1.0.0"
    
    # Descrição
    DESCRIPTION = "API para extração automática de licitações do PNCP e salvamento no Supabase"

# ============================================================================
# CONFIGURAÇÕES DE LOGS
# ============================================================================

class LogConfig:
    """Configurações de logging"""
    
    # Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # Formato do log
    FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Formato de data
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# ============================================================================
# CONFIGURAÇÕES DE MODALIDADES
# ============================================================================

class ModalidadesConfig:
    """Códigos e nomes das modalidades de licitação"""
    
    MODALIDADES = {
        1: "Leilão Eletrônico",
        2: "Diálogo Competitivo",
        3: "Concurso",
        4: "Concorrência Eletrônica",
        5: "Concorrência Presencial",
        6: "Pregão Eletrônico",
        7: "Pregão Presencial",
        8: "Dispensa de Licitação",
        9: "Inexigibilidade",
        10: "Manifestação de Interesse",
        11: "Pré-qualificação",
        12: "Credenciamento",
        13: "Leilão Presencial"
    }
    
    @classmethod
    def get_nome(cls, codigo: int) -> str:
        """Retorna o nome da modalidade pelo código"""
        return cls.MODALIDADES.get(codigo, f"Modalidade {codigo}")
    
    @classmethod
    def get_todas(cls) -> dict:
        """Retorna todas as modalidades"""
        return cls.MODALIDADES

# ============================================================================
# VALIDAÇÃO DAS CONFIGURAÇÕES
# ============================================================================

def validar_configuracoes() -> dict:
    """
    Valida todas as configurações e retorna status
    
    Returns:
        dict: Status de cada configuração
    """
    status = {
        "supabase": {
            "configurado": SupabaseConfig.is_configured(),
            "url": SupabaseConfig.URL if SupabaseConfig.URL else "❌ Não configurado",
            "key": "✓ Configurada" if SupabaseConfig.KEY else "❌ Não configurada",
            "table": SupabaseConfig.TABLE_NAME
        },
        "pncp": {
            "consulta_url": PNCPConfig.CONSULTA_URL,
            "integracao_url": PNCPConfig.INTEGRACAO_URL,
            "timeout": PNCPConfig.REQUEST_TIMEOUT
        },
        "scheduler": {
            "horario": SchedulerConfig.HORARIO_PADRAO,
            "modalidades": SchedulerConfig.MODALIDADES_PADRAO,
            "dias_atras": SchedulerConfig.DIAS_ATRAS
        },
        "servidor": {
            "host": ServerConfig.HOST,
            "port": ServerConfig.PORT,
            "debug": ServerConfig.DEBUG
        }
    }
    
    return status

def exibir_configuracoes():
    """Exibe as configurações atuais no console"""
    print("\n" + "="*70)
    print("⚙️  CONFIGURAÇÕES DA APLICAÇÃO")
    print("="*70)
    
    status = validar_configuracoes()
    
    print("\n📊 SUPABASE:")
    if status['supabase']['configurado']:
        print(f"   ✅ Configurado")
        print(f"   URL: {status['supabase']['url']}")
        print(f"   Key: {status['supabase']['key']}")
        print(f"   Tabela: {status['supabase']['table']}")
    else:
        print(f"   ❌ NÃO CONFIGURADO")
        print(f"   URL: {status['supabase']['url']}")
        print(f"   Key: {status['supabase']['key']}")
        print(f"\n   💡 Configure no arquivo .env ou diretamente no config.py")
    
    print(f"\n🌐 API PNCP:")
    print(f"   Consulta: {status['pncp']['consulta_url']}")
    print(f"   Integração: {status['pncp']['integracao_url']}")
    print(f"   Timeout: {status['pncp']['timeout']}s")
    
    print(f"\n⏰ SCHEDULER:")
    print(f"   Horário: {status['scheduler']['horario']}")
    print(f"   Modalidades: {status['scheduler']['modalidades']}")
    print(f"   Dias atrás: {status['scheduler']['dias_atras']}")
    
    print(f"\n🖥️  SERVIDOR:")
    print(f"   Host: {status['servidor']['host']}")
    print(f"   Porta: {status['servidor']['port']}")
    print(f"   Debug: {status['servidor']['debug']}")
    
    print("\n" + "="*70 + "\n")

# ============================================================================
# EXPORTAÇÃO
# ============================================================================

__all__ = [
    'SupabaseConfig',
    'PNCPConfig',
    'MistralConfig',
    'SchedulerConfig',
    'ServerConfig',
    'LogConfig',
    'ModalidadesConfig',
    'validar_configuracoes',
    'exibir_configuracoes'
]

