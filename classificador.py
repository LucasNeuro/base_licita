"""
Classificador de licitações em 2 etapas para suportar taxonomias grandes (100 setores / 1000 subsetores).

Etapa 1 — Escolhe o SETOR  (~100 opções  → ~2.300 tokens de input)
Etapa 2 — Escolhe o SUBSETOR dentro daquele setor (~10 opções → ~1.200 tokens de input)

Custo estimado por licitação (open-mistral-7b, $0,25/1M tokens): ~$0,000875
"""

import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from mistralai import Mistral
from supabase import Client
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

from config import MistralConfig, SupabaseConfig, ClassificacaoSchedulerConfig

logger = logging.getLogger(__name__)
console = Console()


class MistralUnauthorizedError(Exception):
    """Chave Mistral inválida (401). Interrompe o lote inteiro."""
    pass


class ClassificadorIA:
    """Classificador de licitações em 2 etapas usando Mistral AI."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.client: Optional[Mistral] = None
        self.model = MistralConfig.MODEL

        # Taxonomia carregada uma vez por instância
        # _setores_texto  : texto formatado para o prompt da etapa 1 (lista de setores)
        # _subsetores_por_setor : { setor_id: [ {id, nome, descricao} ] }
        # _setor_nome     : { setor_id: nome }
        # _subsetor_to_setor : { subsetor_id: setor_id }  (para salvar sem query extra)
        self._setores_texto: str = ""
        self._subsetores_por_setor: Dict[str, List[Dict]] = {}
        self._setor_nome: Dict[str, str] = {}
        self._subsetor_to_setor: Dict[str, str] = {}

        if MistralConfig.is_configured():
            try:
                self.client = Mistral(api_key=MistralConfig.API_KEY)
                logger.info(
                    "✅ Cliente Mistral inicializado — modelo: %s | chave: %d chars",
                    self.model, len(MistralConfig.API_KEY),
                )
            except Exception as e:
                logger.error("❌ Erro ao inicializar Mistral: %s", e)
        else:
            logger.warning("⚠️ Mistral não configurada (MISTRAL_API_KEY ausente)")

    # =========================================================================
    # Método principal
    # =========================================================================

    async def classificar_pendentes(
        self,
        limite: int = 50,
        paralelo: int = ClassificacaoSchedulerConfig.PARALELO,
    ) -> Dict[str, int]:
        """
        Busca licitações sem classificação e processa com IA em 2 etapas paralelas.

        Args:
            limite:   Máximo de licitações a processar nesta rodada.
            paralelo: Chamadas simultâneas à Mistral (semáforo).
        """
        if not self.client:
            console.print(Panel(
                "[red]Mistral não configurada — configure MISTRAL_API_KEY.[/red]",
                title="Classificação IA", border_style="red",
            ))
            return {"erro": "Mistral não configurada"}

        stats = {"processados": 0, "sucessos": 0, "falhas": 0}

        # 1. Carregar taxonomia ------------------------------------------------
        ok = self._carregar_taxonomia()
        if not ok:
            console.print(Panel(
                "[red]Não foi possível carregar taxonomia (tabela setores/subsetores vazia ou inacessível).[/red]",
                title="Classificação IA", border_style="red",
            ))
            return stats

        # 2. Buscar licitações pendentes ----------------------------------------
        try:
            response = self.supabase.table(SupabaseConfig.TABLE_NAME) \
                .select("id, objeto_compra, orgao_razao_social, modalidade_nome, itens") \
                .is_("subsetor_principal_id", "null") \
                .limit(limite) \
                .execute()
        except Exception as e:
            logger.error("Erro ao buscar licitações pendentes: %s", e)
            return stats

        licitacoes = response.data or []
        if not licitacoes:
            console.print(Panel(
                "[green]Nenhuma licitação pendente de classificação.[/green]",
                title="Classificação IA", border_style="green",
            ))
            return stats

        total = len(licitacoes)
        n_setores = len(self._setor_nome)
        n_subsetores = len(self._subsetor_to_setor)

        console.print()
        console.print(Panel.fit(
            f"[bold cyan]CLASSIFICAÇÃO EM 2 ETAPAS (IA)[/bold cyan]\n\n"
            f"[yellow]Modelo:[/yellow]       {self.model}\n"
            f"[yellow]Licitações:[/yellow]   {total}\n"
            f"[yellow]Paralelo:[/yellow]     {paralelo} simultâneas\n"
            f"[yellow]Taxonomia:[/yellow]    {n_setores} setores / {n_subsetores} subsetores\n"
            f"[yellow]Etapa 1:[/yellow]      escolher setor  (~2.300 tokens)\n"
            f"[yellow]Etapa 2:[/yellow]      escolher subsetor (~1.200 tokens)\n"
            f"[yellow]Custo est.:[/yellow]   ~${total * 0.000875:.2f} ({total} × $0,000875)",
            border_style="cyan",
            title="🧠 Iniciando",
        ))
        console.print()

        # 3. Processar em paralelo com semáforo --------------------------------
        semaforo = asyncio.Semaphore(paralelo)
        stop_event = asyncio.Event()   # sinaliza parada no 401

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("[cyan]{task.completed}/{task.total}[/cyan]"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Classificando...", total=total)

            async def processar_uma(lic: Dict) -> None:
                if stop_event.is_set():
                    stats["falhas"] += 1
                    progress.update(task_id, advance=1)
                    return

                async with semaforo:
                    if stop_event.is_set():
                        stats["falhas"] += 1
                        progress.update(task_id, advance=1)
                        return
                    try:
                        resultado = await self._classificar_em_2_etapas(lic)
                        if resultado:
                            ok = self._salvar_classificacao(lic["id"], resultado)
                            stats["sucessos" if ok else "falhas"] += 1
                        else:
                            stats["falhas"] += 1
                    except MistralUnauthorizedError:
                        stop_event.set()
                        stats["falhas"] += 1
                        console.print(Panel.fit(
                            "[red]Classificação interrompida: MISTRAL_API_KEY inválida (401).\n"
                            "Corrija a chave e reinicie.[/red]",
                            border_style="red", title="[Mistral 401]",
                        ))
                    except Exception as e:
                        logger.error("Erro ao classificar %s: %s", lic.get("id"), e)
                        stats["falhas"] += 1
                    finally:
                        stats["processados"] += 1
                        progress.update(task_id, advance=1)

            await asyncio.gather(*[processar_uma(lic) for lic in licitacoes])

        # 4. Resumo -----------------------------------------------------------
        taxa = f"{(stats['sucessos'] / total * 100):.1f}%" if total else "0%"
        tbl = Table(title="Resumo da classificação", box=box.ROUNDED, header_style="bold cyan")
        tbl.add_column("Métrica",  style="yellow", width=20)
        tbl.add_column("Valor",    justify="right", style="green")
        tbl.add_row("Processados", str(stats["processados"]))
        tbl.add_row("Sucessos",    f"[bold green]{stats['sucessos']}[/bold green]")
        tbl.add_row("Falhas",      f"[red]{stats['falhas']}[/red]" if stats["falhas"] else "0")
        tbl.add_row("Taxa",        taxa)
        console.print()
        console.print(tbl)
        console.print(Panel.fit(
            f"[bold green]Classificação concluída.[/bold green]  "
            f"Processados: {stats['processados']} | Sucessos: {stats['sucessos']} | Falhas: {stats['falhas']}",
            border_style="green", title="✅ Fim",
        ))
        console.print()
        logger.info("✅ Classificação concluída: %s", stats)
        return stats

    # =========================================================================
    # Etapas de classificação
    # =========================================================================

    async def _classificar_em_2_etapas(self, licitacao: Dict) -> Optional[Dict]:
        """
        Etapa 1: identifica o setor.
        Etapa 2: identifica o subsetor dentro do setor escolhido.
        Retorna dict com subsetor_id, setor_id, confianca, justificativa ou None em caso de falha.
        """
        contexto = self._montar_contexto_licitacao(licitacao)

        # --- Etapa 1: setor --------------------------------------------------
        prompt_setor = self._prompt_etapa1(contexto)
        resp1 = await self._chamar_mistral(prompt_setor)
        if not resp1:
            logger.warning("Etapa 1 falhou para licitação %s", licitacao.get("id"))
            return None

        setor_id = str(resp1.get("setor_id", "")).strip()
        if not setor_id or setor_id not in self._subsetores_por_setor:
            logger.warning(
                "setor_id '%s' inválido na etapa 1 (licitação %s). "
                "Setores válidos: %s",
                setor_id, licitacao.get("id"),
                list(self._subsetores_por_setor.keys())[:5],
            )
            return None

        # --- Etapa 2: subsetor -----------------------------------------------
        subsetores_do_setor = self._subsetores_por_setor[setor_id]
        prompt_subsetor = self._prompt_etapa2(contexto, setor_id, subsetores_do_setor)
        resp2 = await self._chamar_mistral(prompt_subsetor)
        if not resp2:
            logger.warning("Etapa 2 falhou para licitação %s", licitacao.get("id"))
            return None

        subsetor_id = str(resp2.get("subsetor_id", "")).strip()
        validos = {str(s["id"]) for s in subsetores_do_setor}
        if not subsetor_id or subsetor_id not in validos:
            logger.warning(
                "subsetor_id '%s' inválido na etapa 2 (licitação %s).",
                subsetor_id, licitacao.get("id"),
            )
            return None

        return {
            "setor_id":      setor_id,
            "subsetor_id":   subsetor_id,
            "confianca":     float(resp2.get("confianca", resp1.get("confianca", 0.0))),
            "justificativa": (resp2.get("justificativa") or resp1.get("justificativa") or "")[:2000],
        }

    # =========================================================================
    # Taxonomia
    # =========================================================================

    def _carregar_taxonomia(self) -> bool:
        """
        Carrega setores e subsetores do Supabase e monta estruturas para os prompts.
        Retorna True se carregou com sucesso.
        """
        try:
            # --- Setores -------------------------------------------------------
            resp_setores = self.supabase.table("setores") \
                .select("id, nome, descricao") \
                .eq("ativo", True) \
                .execute()
            setores = resp_setores.data or []

            if not setores:
                logger.error("Tabela 'setores' vazia ou inacessível.")
                return False

            self._setor_nome = {str(s["id"]): s["nome"] for s in setores}

            # Texto da etapa 1: "ID: uuid | SETOR: nome - descrição"
            linhas_setores = []
            for s in setores:
                desc = f" - {s['descricao']}" if s.get("descricao") else ""
                linhas_setores.append(f"ID: {s['id']} | {s['nome']}{desc}")
            self._setores_texto = "\n".join(linhas_setores)

            # --- Subsetores ----------------------------------------------------
            resp_sub = self.supabase.table("subsetores") \
                .select("id, nome, descricao, setor_id") \
                .eq("ativo", True) \
                .execute()
            subsetores = resp_sub.data or []

            if not subsetores:
                logger.error("Tabela 'subsetores' vazia ou inacessível.")
                return False

            self._subsetor_to_setor = {}
            self._subsetores_por_setor = {}

            for sub in subsetores:
                sid = str(sub["id"])
                setor_id = str(sub["setor_id"])
                self._subsetor_to_setor[sid] = setor_id
                self._subsetores_por_setor.setdefault(setor_id, []).append(sub)

            logger.info(
                "✅ Taxonomia carregada: %d setores / %d subsetores",
                len(setores), len(subsetores),
            )
            return True

        except Exception as e:
            logger.error("Erro ao carregar taxonomia: %s", e)
            return False

    # =========================================================================
    # Prompts
    # =========================================================================

    def _montar_contexto_licitacao(self, licitacao: Dict) -> str:
        itens_texto = ""
        itens = licitacao.get("itens")
        if isinstance(itens, list) and itens:
            itens_texto = "\n".join(
                f"- {it.get('descricao', '')}" for it in itens[:5]
            )
        return (
            f"OBJETO: {licitacao.get('objeto_compra')}\n"
            f"ÓRGÃO: {licitacao.get('orgao_razao_social')}\n"
            f"MODALIDADE: {licitacao.get('modalidade_nome')}\n"
            f"ITENS PRINCIPAIS:\n{itens_texto}"
        )

    def _prompt_etapa1(self, contexto: str) -> str:
        """Etapa 1 — escolher o SETOR mais adequado."""
        return (
            "Você é um especialista em licitações públicas brasileiras.\n"
            "Analise a licitação e escolha o SETOR mais adequado da lista.\n\n"
            f"LICITAÇÃO:\n{contexto}\n\n"
            f"SETORES DISPONÍVEIS:\n{self._setores_texto}\n\n"
            "Retorne APENAS um JSON válido:\n"
            "{\n"
            '  "setor_id": "UUID_DO_SETOR",\n'
            '  "confianca": 0.90,\n'
            '  "justificativa": "Motivo em 1 frase."\n'
            "}"
        )

    def _prompt_etapa2(self, contexto: str, setor_id: str, subsetores: List[Dict]) -> str:
        """Etapa 2 — escolher o SUBSETOR dentro do setor já identificado."""
        setor_nome = self._setor_nome.get(setor_id, setor_id)
        linhas = []
        for s in subsetores:
            desc = f" - {s['descricao']}" if s.get("descricao") else ""
            linhas.append(f"ID: {s['id']} | {s['nome']}{desc}")
        lista_subsetores = "\n".join(linhas)

        return (
            "Você é um especialista em licitações públicas brasileiras.\n"
            f"O setor já foi identificado como: {setor_nome}.\n"
            "Agora escolha o SUBSETOR mais específico da lista abaixo.\n\n"
            f"LICITAÇÃO:\n{contexto}\n\n"
            f"SUBSETORES DE '{setor_nome}':\n{lista_subsetores}\n\n"
            "Retorne APENAS um JSON válido:\n"
            "{\n"
            '  "subsetor_id": "UUID_DO_SUBSETOR",\n'
            '  "confianca": 0.95,\n'
            '  "justificativa": "Motivo em 1 ou 2 frases."\n'
            "}"
        )

    # =========================================================================
    # Chamada Mistral com retry
    # =========================================================================

    async def _chamar_mistral(self, prompt: str, max_tentativas: int = 3) -> Optional[Dict]:
        """Envia prompt para Mistral com retry para rate limit (429) e erros transitórios."""
        for tentativa in range(1, max_tentativas + 1):
            try:
                resp = await self.client.chat.complete_async(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=MistralConfig.TEMPERATURE,
                )
                return json.loads(resp.choices[0].message.content)

            except Exception as e:
                msg = str(e)

                if "401" in msg or "Unauthorized" in msg:
                    logger.error(
                        "MISTRAL_API_KEY inválida (401). "
                        "Gere nova chave em https://console.mistral.ai/"
                    )
                    raise MistralUnauthorizedError("401 Unauthorized") from e

                if "429" in msg or "rate" in msg.lower() or "too many" in msg.lower():
                    espera = 5 * (2 ** (tentativa - 1))  # 5s → 10s → 20s
                    logger.warning(
                        "⏳ Rate limit Mistral (429) — aguardando %ds (tentativa %d/%d)",
                        espera, tentativa, max_tentativas,
                    )
                    await asyncio.sleep(espera)
                    continue

                if tentativa < max_tentativas:
                    await asyncio.sleep(2)
                    continue

                logger.error("Erro na chamada Mistral após %d tentativas: %s", max_tentativas, e)
                return None

        return None

    # =========================================================================
    # Salvar classificação (sem query extra)
    # =========================================================================

    def _salvar_classificacao(self, licitacao_id: str, resultado: Dict) -> bool:
        """
        Salva a classificação em licitacoes_classificacao e atualiza licitacoes.
        Usa o retorno do upsert para obter o id — sem SELECT extra.
        """
        try:
            subsetor_id   = str(resultado.get("subsetor_id", "")).strip()
            setor_id      = str(resultado.get("setor_id", "")).strip()
            confianca     = float(resultado.get("confianca", 0.0))
            justificativa = (resultado.get("justificativa") or "")[:2000]

            if not subsetor_id or not setor_id:
                logger.warning("Resultado sem subsetor_id ou setor_id para licitacao %s", licitacao_id)
                return False

            # Valida contra taxonomia carregada
            if subsetor_id not in self._subsetor_to_setor:
                logger.error(
                    "subsetor_id '%s' não existe na taxonomia (licitacao %s).",
                    subsetor_id, licitacao_id,
                )
                return False

            dados_vinculo: Dict = {
                "licitacao_id": licitacao_id,
                "setor_id":     setor_id,
                "subsetor_id":  subsetor_id,
                "confianca":    confianca,
                "origem":       "mistral_ai",
                "updated_at":   datetime.now().isoformat(),
            }
            if justificativa:
                dados_vinculo["justificativa"] = justificativa

            # Upsert retorna o registro — evita SELECT extra
            resp_upsert = self.supabase.table("licitacoes_classificacao") \
                .upsert(dados_vinculo, on_conflict="licitacao_id, subsetor_id") \
                .execute()

            classificacao_id = resp_upsert.data[0]["id"] if resp_upsert.data else None
            if not classificacao_id:
                logger.error("Upsert não retornou id para licitacao %s", licitacao_id)
                return False

            # Atualiza atalhos na tabela principal
            self.supabase.table("licitacoes").update({
                "classificacao_principal_id": classificacao_id,
                "setor_principal_id":         setor_id,
                "subsetor_principal_id":      subsetor_id,
            }).eq("id", licitacao_id).execute()

            logger.info(
                "✅ Classificado: %s → setor=%s subsetor=%s confiança=%.2f",
                licitacao_id, setor_id, subsetor_id, confianca,
            )
            return True

        except Exception as e:
            logger.error("Erro ao salvar classificação para %s: %s", licitacao_id, e)
            return False
