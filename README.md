# Vigía de Continuidad

El panel muestra en vivo el estado de **Genesys Cloud (use1 y sae1)**, **AWS (us-east-1 y sa-east-1)** y **Azure / Entra ID**. Cuando algo nos afecta, avisa por **Telegram**.

- Revisa las fuentes cada 5 minutos con GitHub Actions (gratis en repos públicos).
- Publica el panel en GitHub Pages: `https://TU-USUARIO.github.io/NOMBRE-DEL-REPO/`.
- Avisa a Telegram solo en estos casos:
  - el nivel pasa a **degradado** o **caída**;
  - aparece un incidente nuevo;
  - el problema sigue después de 3 horas;
  - se recupera;
  - hay una mantención nueva en las próximas 24 h;
  - no se pudo leer Genesys.
- No usa credenciales: todas las fuentes son públicas. Los únicos secretos son el token y el chat de Telegram, y se guardan como *secrets* de GitHub.

## Contenido

| Archivo | Qué hace |
|---|---|
| `vigia.py` | Revisa las fuentes, clasifica, decide si avisa y escribe `docs/estado.json` |
| `docs/index.html` | El panel. Lee `estado.json` y se refresca solo cada minuto |
| `docs/estado.json` | Último estado e historial de 24 h (lo actualiza el robot) |
| `.github/workflows/vigia.yml` | La tarea programada cada 5 minutos |

---

## Puesta en marcha (unos 15 minutos)

### 1. Crear el bot de Telegram

1. En Telegram, abre **@BotFather**, escribe `/newbot` y sigue los pasos. Al final te entrega un **token**, parecido a `123456789:AAH...`.
2. Abre el chat con tu bot nuevo y mándale cualquier mensaje, por ejemplo "hola". Si quieres que los avisos lleguen a un **grupo** del equipo, agrega el bot al grupo y escribe algo ahí.
3. En el navegador, abre la siguiente dirección, reemplazando `TOKEN` por el tuyo:
   `https://api.telegram.org/botTOKEN/getUpdates`
   Busca `"chat":{"id": ...}`. Ese número es tu **chat id**. En los grupos empieza con `-`.

### 2. Crear el repositorio

1. En GitHub: **New repository**, con un nombre como `vigia-continuidad`, y márcalo como **Public**.
   - Tiene que ser público porque en un repo privado la tarea cada 5 min supera los minutos gratis, y Pages en privado es de pago.
   - El panel queda visible para quien tenga el enlace. La información ya es pública, pero sí revela qué regiones usamos.
2. Sube los archivos con **Add file → Upload files** y arrastra todo el contenido de la carpeta.
   - La carpeta `.github` suele estar oculta en Windows y Mac. Si no se sube, créala a mano: **Add file → Create new file**, escribe como nombre `.github/workflows/vigia.yml` y pega el contenido del archivo.

### 3. Guardar los secretos

En el repo: **Settings → Secrets and variables → Actions → New repository secret**. Crea dos:

- `TELEGRAM_TOKEN`: el token del paso 1.
- `TELEGRAM_CHAT_ID`: el chat id del paso 1.

### 4. Activar GitHub Pages

**Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 5. Primera ejecución y prueba

1. Pestaña **Actions**. Si pide habilitar los workflows, acepta.
2. Elige **Vigía → Run workflow**, marca **"Mandar un mensaje de prueba a Telegram"** y ejecútalo.
3. Revisa tres cosas:
   - Te llega el mensaje de prueba a Telegram.
   - En el log del paso "Revisar fuentes y avisar" aparece una línea `Nivel: ok ...`.
   - El panel abre en `https://TU-USUARIO.github.io/vigia-continuidad/`.

Desde ahí corre solo cada 5 minutos.

---

## Cosas a saber

- **Atrasos de GitHub:** el horario de GitHub no es exacto. En horas de mucha carga una ejecución puede atrasarse varios minutos o saltarse. Si el panel lleva más de 20 min sin actualizarse, se pone gris y lo dice.
- **Pausa por inactividad:** GitHub pausa las tareas programadas de repos sin actividad por 60 días. El robot hace un commit en cada revisión, lo que debería evitarlo. Si alguna vez se pausa, ve a **Actions → Vigía → Enable workflow**.
- **Si el script falla:** GitHub te manda un correo de "workflow run failed". Ese es el respaldo si se cae el propio vigía.
- **Mismo criterio que la tarea de Claude:**
  - Niveles: `caida > degradado > aviso > mant > ok`.
  - El nivel general es el peor entre Genesys use1/sae1, AWS us-east-1/sa-east-1 y Azure/Entra.
  - Un evento de AWS en nuestras regiones cuenta como abierto si es de las últimas 24 h y no tiene mensaje `[RESOLVED]`.
- **Fuente de Genesys:** el vigía lee la API pública de la página de estado (`status.mypurecloud.com/api/v2/summary.json`). Es la misma información que muestra la página, en formato de datos.

## Cambios comunes

- **Otra región:** edita `REGIONES` al inicio de `vigia.py`. El nombre del grupo va tal como aparece en status.mypurecloud.com, en minúsculas y sin tildes.
- **Otra frecuencia:** cambia `*/5` en `.github/workflows/vigia.yml`. Por ejemplo, `*/10` revisa cada 10 minutos.
- **Repetir el aviso con otra frecuencia:** cambia `REAVISO` en `vigia.py`. Por defecto son 3 h.
- **Enlace del panel en los avisos:** se deduce solo. Si usas un dominio propio, crea una variable `PANEL_URL` en *Settings → Secrets and variables → Actions → Variables*.
- **Carrier BYOC:** cuando sepamos cuál es, se agrega como una fuente más en `vigia.py`.

## Probarlo en tu computador (opcional)

```bash
python3 vigia.py   # sin TELEGRAM_* solo imprime el aviso que mandaría
```
