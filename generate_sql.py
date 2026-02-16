
import csv
import os

def escape_string(value):
    if value is None or value == "":
        return "NULL"
    return "'" + value.replace("'", "''") + "'"

def generate_sql():
    setores_csv = r"c:\Users\anima\Downloads\setores_rows (3).csv"
    subsetores_csv = r"c:\Users\anima\Downloads\subsetores_rows (1).csv"
    output_file = r"c:\Users\anima\OneDrive\Desktop\vamos\base_licita\populate_setores.sql"

    with open(output_file, 'w', encoding='utf-8') as sql_file:
        sql_file.write("-- Script gerado automaticamente para popular setores e subsetores\n\n")
        
        # Processar Setores
        sql_file.write("-- SETORES\n")
        try:
            with open(setores_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    id_val = escape_string(row['id'])
                    nome = escape_string(row['nome'])
                    descricao = escape_string(row['descricao'])
                    ativo = row['ativo'] if row['ativo'] else 'true'
                    ordem = row['ordem'] if row['ordem'] else '0'
                    created_at = escape_string(row['created_at'])
                    updated_at = escape_string(row['updated_at'])

                    sql = f"INSERT INTO public.setores (id, nome, descricao, ativo, ordem, created_at, updated_at) VALUES ({id_val}, {nome}, {descricao}, {ativo}, {ordem}, {created_at}, {updated_at}) ON CONFLICT (id) DO NOTHING;\n"
                    sql_file.write(sql)
        except Exception as e:
            print(f"Erro ao processar setores: {e}")

        sql_file.write("\n-- SUBSETORES\n")
        try:
            with open(subsetores_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    id_val = escape_string(row['id'])
                    setor_id = escape_string(row['setor_id'])
                    nome = escape_string(row['nome'])
                    descricao = escape_string(row['descricao'])
                    ativo = row['ativo'] if row['ativo'] else 'true'
                    ordem = row['ordem'] if row['ordem'] else '0'
                    created_at = escape_string(row['created_at'])
                    updated_at = escape_string(row['updated_at'])

                    sql = f"INSERT INTO public.subsetores (id, setor_id, nome, descricao, ativo, ordem, created_at, updated_at) VALUES ({id_val}, {setor_id}, {nome}, {descricao}, {ativo}, {ordem}, {created_at}, {updated_at}) ON CONFLICT (id) DO NOTHING;\n"
                    sql_file.write(sql)
        except Exception as e:
            print(f"Erro ao processar subsetores: {e}")

    print(f"Arquivo SQL gerado com sucesso em: {output_file}")

if __name__ == "__main__":
    generate_sql()
