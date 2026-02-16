
import os

def combine_sql():
    populate_file = r"c:\Users\anima\OneDrive\Desktop\vamos\base_licita\populate_setores.sql"
    output_file = r"c:\Users\anima\OneDrive\Desktop\vamos\base_licita\setup_database.sql"

    ddl = """-- Habilita função gen_random_uuid() (recomendado no Supabase)
create extension if not exists pgcrypto;

-- ============================================================
-- Funções de trigger
-- ============================================================

-- Atualiza campo updated_at em setores/subsetores
create or replace function update_setores_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- Atualiza campo data_atualizacao em licitacoes
create or replace function update_licitacoes_atualizacao()
returns trigger
language plpgsql
as $$
begin
  new.data_atualizacao := now();
  return new;
end;
$$;

-- ============================================================
-- Tabela: setores
-- ============================================================
create table if not exists public.setores (
  id uuid not null default gen_random_uuid(),
  nome text not null,
  descricao text null,
  ativo boolean not null default true,
  ordem integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint setores_pkey primary key (id),
  constraint setores_nome_key unique (nome)
);

create index if not exists idx_setores_ativo on public.setores (ativo) where (ativo = true);
create index if not exists idx_setores_ordem on public.setores (ordem);

drop trigger if exists trigger_update_setores_updated_at on public.setores;
create trigger trigger_update_setores_updated_at
before update on public.setores
for each row
execute function update_setores_updated_at();

-- ============================================================
-- Tabela: subsetores
-- ============================================================
create table if not exists public.subsetores (
  id uuid not null default gen_random_uuid(),
  setor_id uuid not null,
  nome text not null,
  descricao text null,
  ativo boolean not null default true,
  ordem integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint subsetores_pkey primary key (id),
  constraint subsetores_setor_id_fkey foreign key (setor_id) references public.setores(id) on delete cascade
);

create index if not exists idx_subsetores_setor_id on public.subsetores (setor_id);
create index if not exists idx_subsetores_ativo on public.subsetores (ativo) where (ativo = true);
create index if not exists idx_subsetores_ordem on public.subsetores (ordem);

drop trigger if exists trigger_update_subsetores_updated_at on public.subsetores;
create trigger trigger_update_subsetores_updated_at
before update on public.subsetores
for each row
execute function update_setores_updated_at();

-- ============================================================
-- Tabela: licitacoes
-- ============================================================
create table if not exists public.licitacoes (
  id uuid not null default gen_random_uuid(),
  numero_controle_pncp text not null,
  id_pncp text null,
  objeto_compra text null,
  valor_total_estimado numeric(15,4) null,
  data_publicacao_pncp date null,
  orgao_razao_social text null,
  uf_sigla text null,
  modalidade_nome text null,
  dados_completos jsonb null default '{}'::jsonb,
  itens jsonb null default '[]'::jsonb,
  anexos jsonb null default '[]'::jsonb,
  historico jsonb null default '[]'::jsonb,
  data_inclusao timestamptz null default now(),
  data_atualizacao timestamptz null default now(),
  link_portal_pncp text null,
  constraint licitacoes_pkey1 primary key (id),
  constraint licitacoes_id_pncp_key unique (id_pncp),
  constraint licitacoes_numero_controle_pncp_key1 unique (numero_controle_pncp)
);

-- Índices
create index if not exists idx_lic_numero_controle on public.licitacoes (numero_controle_pncp);
create index if not exists idx_lic_id_pncp on public.licitacoes (id_pncp) where (id_pncp is not null);
create index if not exists idx_lic_data_pub on public.licitacoes (data_publicacao_pncp);
create index if not exists idx_lic_valor on public.licitacoes (valor_total_estimado);
create index if not exists idx_lic_uf on public.licitacoes (uf_sigla);
create index if not exists idx_lic_modalidade on public.licitacoes (modalidade_nome);

-- Full-text search (Português)
create index if not exists idx_lic_objeto_fulltext
on public.licitacoes using gin (to_tsvector('portuguese', objeto_compra));

create index if not exists idx_lic_orgao_fulltext
on public.licitacoes using gin (to_tsvector('portuguese', orgao_razao_social));

-- JSONB
create index if not exists idx_lic_dados_completos on public.licitacoes using gin (dados_completos);
create index if not exists idx_lic_itens on public.licitacoes using gin (itens);
create index if not exists idx_lic_anexos on public.licitacoes using gin (anexos);
create index if not exists idx_lic_historico on public.licitacoes using gin (historico);

create index if not exists idx_lic_link_portal on public.licitacoes (link_portal_pncp);

-- Trigger para manter data_atualizacao
drop trigger if exists trigger_update_licitacoes on public.licitacoes;
create trigger trigger_update_licitacoes
before update on public.licitacoes
for each row
execute function update_licitacoes_atualizacao();

"""

    try:
        with open(output_file, 'w', encoding='utf-8') as outfile:
            outfile.write(ddl)
            outfile.write("\n\n-- ============================================================\n")
            outfile.write("-- POPULAÇÃO DE DADOS (SETORES E SUBSETORES)\n")
            outfile.write("-- ============================================================\n\n")
            
            with open(populate_file, 'r', encoding='utf-8') as infile:
                outfile.write(infile.read())
        
        print(f"Arquivo SQL completo gerado em: {output_file}")
    except Exception as e:
        print(f"Erro ao combinar arquivos: {e}")

if __name__ == "__main__":
    combine_sql()
