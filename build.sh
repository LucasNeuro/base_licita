#!/usr/bin/env bash
# build.sh - Script de build para Render

set -o errexit

echo "🔨 Atualizando pip..."
pip install --upgrade pip

echo "📦 Instalando dependências..."
pip install -r requirements.txt

echo "✅ Build concluído com sucesso!"
