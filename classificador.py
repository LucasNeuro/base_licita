import json
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Any
from mistralai import Mistral
from supabase import Client

from config import MistralConfig, SupabaseConfig

# Configuração de logs
logger = logging.getLogger(__name__)

class ClassificadorIA:
    """Classificador de licitações usando Mistral AI"""
    
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.client = None
        self.model = MistralConfig.MODEL
        
        if MistralConfig.is_configured():
            try:
                self.client = Mistral(api_key=MistralConfig.API_KEY)
                logger.info("✅ Cliente Mistral AI inicializado")
            except Exception as e:
                logger.error(f"❌ Erro ao inicializar Mistral: {e}")
        else:
            logger.warning("⚠️ Mistral AI não configurada (MISTRAL_API_KEY ausente)")
    
    async def classificar_pendentes(self, limite: int = 50) -> Dict[str, int]:
        """
        Busca licitações sem classificação e processa com IA
        
        Returns:
            Dict com estatísticas (processados, sucessos, falhas)
        """
        if not self.client:
            return {"erro": "Mistral não configurado"}
            
        stats = {"processados": 0, "sucessos": 0, "falhas": 0}
        
        # 1. Carregar setores e subsetores para contexto (cache simples)
        setores_map = self._carregar_taxonomia()
        if not setores_map:
            logger.error("❌ Não foi possível carregar taxonomia de setores")
            return stats
            
        # 2. Buscar licitações pendentes
        try:
            # Busca onde subsetor_principal_id é null
            response = self.supabase.table(SupabaseConfig.TABLE_NAME)\
                .select("id, objeto_compra, orgao_razao_social, modalidade_nome, itens")\
                .is_("subsetor_principal_id", "null")\
                .limit(limite)\
                .execute()
                
            licitacoes = response.data
            
            if not licitacoes:
                logger.info("🎉 Nenhuma licitação pendente de classificação")
                return stats
                
            logger.info(f"🧠 Classificando {len(licitacoes)} licitações...")
            
            # 3. Processar cada licitação
            for licitacao in licitacoes:
                stats["processados"] += 1
                
                try:
                    # Monta prompt
                    prompt = self._montar_prompt(licitacao, setores_map)
                    
                    # Chama Mistral
                    resposta_ia = await self._chamar_mistral(prompt)
                    
                    if resposta_ia:
                        # Salva resultado
                        sucesso = self._salvar_classificacao(licitacao['id'], resposta_ia)
                        if sucesso:
                            stats["sucessos"] += 1
                        else:
                            stats["falhas"] += 1
                    else:
                        stats["falhas"] += 1
                        
                except Exception as e:
                    logger.error(f"Erro ao classificar licitação {licitacao.get('id')}: {e}")
                    stats["falhas"] += 1
                    
        except Exception as e:
            logger.error(f"Erro no fluxo de classificação: {e}")
            
        return stats

    def _carregar_taxonomia(self) -> str:
        """Carrega lista de setores/subsetores formatada para o prompt"""
        try:
            # Busca subsetores ativos com seus setores
            response = self.supabase.table("subsetores")\
                .select("id, nome, descricao, setores(nome)")\
                .eq("ativo", True)\
                .execute()
                
            subsetores = response.data
            
            if not subsetores:
                return None
                
            # Formata para texto: "ID: Nome (Setor) - Descrição"
            lista_texto = []
            for sub in subsetores:
                setor_nome = sub['setores']['nome'] if sub.get('setores') else "Geral"
                desc = f" - {sub['descricao']}" if sub.get('descricao') else ""
                
                linha = f"ID: {sub['id']} | SETOR: {setor_nome} -> {sub['nome']}{desc}"
                lista_texto.append(linha)
                
            return "\n".join(lista_texto)
            
        except Exception as e:
            logger.error(f"Erro ao carregar taxonomia: {e}")
            return None

    def _montar_prompt(self, licitacao: Dict, taxonomia: str) -> str:
        """Cria o prompt para a IA"""
        
        # Resumo dos itens (primeiros 5 para não estourar token)
        itens_texto = ""
        if licitacao.get('itens'):
            itens_lista = licitacao['itens']
            if isinstance(itens_lista, list):
                resumo_itens = [f"- {item.get('descricao', '')}" for item in itens_lista[:5]]
                itens_texto = "\n".join(resumo_itens)
        
        texto_licitacao = f"""
        OBJETO: {licitacao.get('objeto_compra')}
        ÓRGÃO: {licitacao.get('orgao_razao_social')}
        MODALIDADE: {licitacao.get('modalidade_nome')}
        ITENS PRINCIPAIS:
        {itens_texto}
        """
        
        prompt = f"""
        Você é um especialista em classificação de licitações públicas.
        Sua tarefa é analisar a licitação abaixo e escolher o MELHOR subsetor para ela na lista fornecida.
        
        DADOS DA LICITAÇÃO:
        {texto_licitacao}
        
        LISTA DE SUBSETORES (Use APENAS um destes IDs):
        {taxonomia}
        
        INSTRUÇÕES:
        1. Analise o objeto e os itens.
        2. Escolha o subsetor mais específico que se aplica.
        3. Retorne APENAS um JSON no seguinte formato, sem explicações adicionais:
        {{
            "subsetor_id": "UUID_DO_SUBSETOR_ESCOLHIDO",
            "confianca": 0.95,
            "justificativa": "Breve explicação em 1 ou 2 frases do porquê deste subsetor para esta licitação."
        }}
        O campo justificativa é obrigatório e será salvo no banco.
        """
        return prompt

    async def _chamar_mistral(self, prompt: str) -> Optional[Dict]:
        """Envia prompt para Mistral e faz parse do JSON"""
        try:
            chat_response = await self.client.chat.complete_async(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                response_format={"type": "json_object"},
                temperature=MistralConfig.TEMPERATURE,
            )
            
            conteudo = chat_response.choices[0].message.content
            return json.loads(conteudo)
            
        except Exception as e:
            logger.error(f"Erro na chamada Mistral: {e}")
            return None

    def _salvar_classificacao(self, licitacao_id: str, resultado: Dict) -> bool:
        """Salva o resultado no Supabase"""
        try:
            subsetor_id = resultado.get("subsetor_id")
            confianca = resultado.get("confianca", 0.0)
            justificativa = resultado.get("justificativa") or ""
            if isinstance(justificativa, str) and len(justificativa) > 2000:
                justificativa = justificativa[:2000]
            
            if not subsetor_id:
                return False
                
            # 1. Buscar setor_id do subsetor
            resp_sub = self.supabase.table("subsetores").select("setor_id").eq("id", subsetor_id).single().execute()
            if not resp_sub.data:
                logger.error(f"Subsetor {subsetor_id} não encontrado")
                return False
                
            setor_id = resp_sub.data["setor_id"]
            
            # 2. Inserir na tabela de vínculo (upsert), incluindo justificativa/descrição
            dados_vinculo = {
                "licitacao_id": licitacao_id,
                "setor_id": setor_id,
                "subsetor_id": subsetor_id,
                "confianca": confianca,
                "origem": "mistral_ai",
                "updated_at": datetime.now().isoformat()
            }
            if justificativa:
                dados_vinculo["justificativa"] = justificativa
            
            # Upsert na tabela de classificação
            self.supabase.table("licitacoes_classificacao")\
                .upsert(dados_vinculo, on_conflict="licitacao_id, subsetor_id")\
                .execute()
                
            # 3. Atualizar licitação principal (atalho)
            # Primeiro buscamos o ID da classificação recém criada/atualizada
            resp_class = self.supabase.table("licitacoes_classificacao")\
                .select("id")\
                .eq("licitacao_id", licitacao_id)\
                .eq("subsetor_id", subsetor_id)\
                .single().execute()
                
            if resp_class.data:
                classificacao_id = resp_class.data["id"]
                
                self.supabase.table("licitacoes").update({
                    "classificacao_principal_id": classificacao_id,
                    "setor_principal_id": setor_id,
                    "subsetor_principal_id": subsetor_id
                }).eq("id", licitacao_id).execute()
                
                logger.info(f"✅ Licitação {licitacao_id} classificada: {subsetor_id} ({confianca})")
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Erro ao salvar classificação: {e}")
            return False
