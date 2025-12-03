@echo off
echo ============================================================
echo    📦 ENVIANDO CÓDIGO PARA GITHUB
echo ============================================================
echo.

echo 1. Inicializando repositório Git...
git init

echo.
echo 2. Adicionando todos os arquivos...
git add .

echo.
echo 3. Fazendo commit...
git commit -m "feat: API completa de licitações PNCP com scheduler e Rich console"

echo.
echo 4. Configurando branch main...
git branch -M main

echo.
echo 5. Adicionando remote origin...
git remote add origin https://github.com/LucasNeuro/base_licita.git

echo.
echo 6. Enviando para GitHub...
git push -u origin main

echo.
echo ============================================================
echo    ✅ CÓDIGO ENVIADO COM SUCESSO!
echo ============================================================
echo.
echo 🌐 Repositório: https://github.com/LucasNeuro/base_licita
echo.
pause

