# pkg-tracker-bot

Bot de Telegram: le mandás un código de seguimiento y te devuelve transportista + movimientos, usando 17TRACK como agregador (detecta el courier automáticamente entre cientos de transportistas, sin tener que integrar cada uno a mano).

## Setup local

1. Creá el bot en Telegram: hablale a [@BotFather](https://t.me/BotFather), `/newbot`, guardá el token.
2. Registrate en [17TRACK](https://api.17track.net/) y sacá una API key (tier gratis, ~100 registros/mes).
3. Copiá `.env.example` a `.env` y completá los dos valores.
4. ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python bot.py
   ```
5. Hablale al bot en Telegram, mandale un código de tracking.

> Nota: la API de 17TRACK puede requerir aprobación/cambios de endpoint según el plan — si `tracking.py` falla, revisá el dashboard de 17TRACK para el formato vigente. `TrackingClient` está aislado en un solo archivo justamente para poder cambiar de proveedor (TrackingMore, Ship24, etc.) sin tocar `bot.py`.

## Dónde correrlo gratis

El bot usa **long polling** (no necesita IP pública ni HTTPS), así que corre en cualquier máquina con salida a internet. Opciones gratis, de mejor a peor para este caso:

1. **Oracle Cloud Free Tier (recomendado)** — VM ARM "Always Free" (hasta 4 OCPU / 24 GB RAM), gratis para siempre, sin límite de tiempo. Es la única nube grande con free tier realmente permanente y con recursos de sobra para esto. Se instala como cualquier VM Linux: subís el proyecto, instalás Python, y corrés el bot con el `pkg-tracker-bot.service` incluido (`systemctl enable --now pkg-tracker-bot`).
2. **Google Cloud Free Tier** — instancia `e2-micro` siempre gratis, pero limitada a ciertas regiones de EEUU y con menos recursos que Oracle.
3. **Fly.io** — tiene una asignación gratuita chica (suficiente para un proceso liviano como este), pero es una franja de crédito, no "gratis para siempre" garantizado — conviene revisar el pricing vigente antes de depender de él.

Evitá Render/Railway free tier para este caso: son pensados para servicios HTTP y "duermen" el proceso si no reciben tráfico web, lo cual mata el long polling.

## Deploy en Oracle Cloud (resumen)

```bash
# en la VM
sudo apt update && sudo apt install -y python3-venv git
git clone <tu-repo> pkg-tracker-bot && cd pkg-tracker-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # completar tokens
sudo cp pkg-tracker-bot.service /etc/systemd/system/
sudo systemctl enable --now pkg-tracker-bot@$USER
```
