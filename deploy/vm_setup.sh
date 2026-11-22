#!/usr/bin/env bash
# Instalacja JaysUncle na serwerze oceny (Ubuntu 24.04 + Docker), zgodnie z VM.md organizatora.
#   git clone https://github.com/virescentt/JaysUncle /opt/solution/repo
#   bash /opt/solution/repo/deploy/vm_setup.sh
# Buduje obraz, kopiuje RUN.md, tworzy .env i sprawdza komendę z RUN.md na przykładowej sprawie.
set -euo pipefail
SOL="${SOL:-/opt/solution}"
REPO="${REPO:-$SOL/repo}"
SUDO="${SUDO-sudo}"

echo "==> katalogi w $SOL"
mkdir -p "$SOL/input" "$SOL/output"
cp "$REPO/RUN.md" "$SOL/RUN.md"
if [ ! -f "$SOL/.env" ]; then
  cp "$REPO/.env.example" "$SOL/.env"
  echo "    utworzono $SOL/.env z .env.example — wpisz LLM_API_KEY (nano $SOL/.env)"
fi

echo "==> budowanie obrazu jaysuncle:latest (kilka minut)"
docker build -t jaysuncle:latest "$REPO"

echo "==> test komendy z RUN.md na przykładowej sprawie"
$SUDO rm -rf "$SOL/input/_test_jaysuncle" "$SOL/output/_test_jaysuncle"
mkdir -p "$SOL/input/_test_jaysuncle"
cp "$REPO"/input/przyklad_01/*.pdf "$SOL/input/_test_jaysuncle/"
(cd "$SOL" && $SUDO docker run --rm --env-file "$SOL/.env" -v "$SOL/input":/app/input -v "$SOL/output":/app/output jaysuncle:latest)
echo "==> wyniki testu:"
ls -la "$SOL/output/_test_jaysuncle"
for f in "$SOL"/output/_test_jaysuncle/*.json; do echo "--- $f"; head -c 300 "$f"; echo; done

echo "==> sprzątanie po teście (input/ i output/ zostają puste dla oceniających)"
$SUDO rm -rf "$SOL/input/_test_jaysuncle" "$SOL/output/_test_jaysuncle"
echo "GOTOWE. Komenda oceny jest w $SOL/RUN.md"
