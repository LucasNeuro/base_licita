#!/usr/bin/env bash
# build.sh - Script de build para Render

set -o errexit

echo "🔨 Instalando dependências..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Build concluído com sucesso!"

