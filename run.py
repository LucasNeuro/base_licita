"""
Script para iniciar a API PNCP Licitações
Execute: python run.py
"""

import uvicorn
from config import ServerConfig, LogConfig

if __name__ == "__main__":
    print("="*70)
    print(f"🚀 Iniciando {ServerConfig.APP_NAME}")
    print("="*70)
    print()
    print(f"📡 API rodará em: http://{ServerConfig.HOST}:{ServerConfig.PORT}")
    print(f"📚 Swagger UI: http://localhost:{ServerConfig.PORT}/docs")
    print(f"📖 ReDoc: http://localhost:{ServerConfig.PORT}/redoc")
    print()
    print("="*70)
    print()
    
    # Configurações do servidor
    uvicorn.run(
        "main:app",
        host=ServerConfig.HOST,
        port=ServerConfig.PORT,
        reload=ServerConfig.DEBUG,  # Auto-reload quando o código mudar
        log_level=LogConfig.LEVEL.lower()
    )

