#!/usr/bin/env python3
"""Vigía de continuidad operacional.

Revisa Genesys Cloud (use1 y sae1), AWS (us-east-1 y sa-east-1) y Azure/Entra ID,
guarda el resultado en docs/estado.json (lo lee el panel) y avisa por Telegram
solo cuando algo nos afecta o cambia.

Solo usa la librería estándar de Python 3.11+.
Variables de entorno:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  -> para los avisos (opcionales: sin ellas solo se imprime el aviso)
  PANEL_URL                         -> enlace al panel en los avisos (opcional; se deduce en GitHub)
  VIGIA_PRUEBA=true                 -> manda un mensaje de prueba a Telegram y sigue normal
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- configuración
RUTA_ESTADO = os.environ.get("VIGIA_ESTADO", "docs/estado.json")

GC_API = "https://status.mypurecloud.com/api/v2/summary.json"
GC_URL = "https://status.mypurecloud.com/"
AWS_RSS = "https://status.aws.amazon.com/rss/all.rss"
AWS_URL = "https://health.aws.amazon.com/health/status"
AZ_RSS = "https://azure.status.microsoft/en-us/status/feed/"
AZ_URL = "https://azure.status.microsoft/"
M365_URL = "https://status.cloud.microsoft/"

# Regiones que nos importan. 'gc' = nombre del grupo en status.mypurecloud.com (sin tildes, minúsculas).
REGIONES = {
    "use1": {"gc": "americas (us east)", "aws": "us-east-1", "titulo": ("us-east", "us east")},
    "sae1": {"gc": "americas (sao paulo)", "aws": "sa-east-1", "titulo": ("sao paulo", "sa-east")},
}

ORDEN = ["ok", "mant", "aviso", "degradado", "caida"]
HISTORIAL_MAX = 288            # 24 h con revisiones cada 5 min
REAVISO = timedelta(hours=3)   # repetir aviso si el problema sigue
AWS_VENTANA = timedelta(hours=24)
OTROS_VENTANA = timedelta(hours=72)  # eventos de otras regiones: solo si son recientes
TZ_CHILE = ZoneInfo("America/Santiago")

# Palabras que, si aparecen en el feed de Azure, lo hacen relevante para nosotros
AZ_CLAVE = ("entra", "azure active directory", "azure ad", "east us", "brazil", "multiple regions",
            "global", "teams", "microsoft 365", "office 365")
AZ_M365 = ("teams", "microsoft 365", "office 365")

COMP_ESTADO = {"operational": "ok", "under_maintenance": "mant", "degraded_performance": "degradado",
               "partial_outage": "degradado", "major_outage": "caida"}
COMP_TXT = {"operational": "Operativo", "under_maintenance": "Under Maintenance",
            "degraded_performance": "Degraded Performance", "partial_outage": "Partial Outage",
            "major_outage": "Major Outage"}
IMPACTO = {"critical": "caida", "major": "degradado", "minor": "aviso", "none": "aviso", "maintenance": "mant"}
INC_TXT = {"investigating": "Investigando", "identified": "Identificado", "monitoring": "En monitoreo",
           "resolved": "Resuelto", "postmortem": "Postmortem", "scheduled": "Mantención programada",
           "in_progress": "Mantención en curso", "verifying": "Verificando", "completed": "Completada"}
NIVEL_TXT = {"ok": "Operativo", "mant": "Mantención", "aviso": "Aviso", "degradado": "Degradado",
             "caida": "CAÍDA", "sin_dato": "Sin datos"}


# ---------------------------------------------------------------- utilidades
def ahora():
    forzado = os.environ.get("VIGIA_AHORA")  # solo para pruebas
    return leer_iso(forzado) if forzado else datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def leer_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hora_chile(dt):
    return dt.astimezone(TZ_CHILE).strftime("%d-%m %H:%M") if dt else "?"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().strip()


def peor(estados):
    validos = [e for e in estados if e in ORDEN]
    return max(validos, key=ORDEN.index) if validos else "ok"


def bajar(url):
    """Descarga con un reintento. Devuelve texto o None."""
    fixtures = os.environ.get("VIGIA_FIXTURES")  # solo para pruebas
    if fixtures:
        nombre = {GC_API: "gc.json", AWS_RSS: "aws.rss", AZ_RSS: "azure.rss"}[url]
        ruta = os.path.join(fixtures, nombre)
        return open(ruta, encoding="utf-8").read() if os.path.exists(ruta) else None
    for intento in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vigia-continuidad/1.0",
                                                       "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"[aviso] {url} falló (intento {intento + 1}): {e}", file=sys.stderr)
            time.sleep(4)
    return None


def servicio(id_, grupo, nombre, region, estado, nota, afectados, url):
    return {"id": id_, "grupo": grupo, "n": nombre, "region": region, "estado": estado,
            "nota": nota, "afectados": afectados, "url": url}


# ---------------------------------------------------------------- Genesys Cloud
def revisar_genesys(texto, now):
    """Devuelve (servicios_por_region, incidentes, otros)."""
    nombres = {"use1": "Genesys Cloud · Americas (US East)", "sae1": "Genesys Cloud · Americas (São Paulo)"}
    sin = {r: servicio(f"gc-{r}", "Genesys Cloud", nombres[r], r, "sin_dato",
                       "No se pudo consultar la fuente", [], GC_URL) for r in REGIONES}
    if not texto:
        return sin, [], [], False
    try:
        datos = json.loads(texto)
    except ValueError:
        return sin, [], [], False

    comps = datos.get("components") or []
    grupos = {c["id"]: c for c in comps if c.get("group")}

    def region_por_nombre(nombre):
        n = norm(nombre)
        for r, cfg in REGIONES.items():
            if n == cfg["gc"]:
                return r
        return None

    def parece_region(nombre):
        return bool(nombre) and "(" in nombre and nombre.strip().endswith(")")

    # La página puede agrupar por región (grupo "Americas (US East)" con hijos "Platform"...)
    # o por componente (grupo "Platform" con hijos "Americas (US East)"...). Se aceptan ambas.
    region_comp, etiqueta, region_txt = {}, {}, {}
    for c in comps:
        if c.get("group"):
            continue
        grupo = (grupos.get(c.get("group_id")) or {}).get("name")
        r_grupo, r_hijo = region_por_nombre(grupo), region_por_nombre(c.get("name"))
        if r_grupo:
            region_comp[c["id"]], etiqueta[c["id"]] = r_grupo, c.get("name")
        elif r_hijo:
            region_comp[c["id"]], etiqueta[c["id"]] = r_hijo, grupo or c.get("name")
        else:
            etiqueta[c["id"]] = c.get("name")
        region_txt[c["id"]] = (c.get("name") if parece_region(c.get("name"))
                               else grupo if parece_region(grupo) else c.get("name"))
    for gid, g in grupos.items():
        r = region_por_nombre(g.get("name"))
        if r:
            region_comp[gid] = r
        region_txt[gid] = g.get("name")

    def regiones_de(evento):
        regs, otras = set(), set()
        for c in evento.get("components") or []:
            r = region_comp.get(c.get("id"))
            if r:
                regs.add(r)
            elif region_txt.get(c.get("id")):
                otras.add(region_txt[c["id"]])
        t = norm(evento.get("name"))
        for r, cfg in REGIONES.items():
            if any(k in t for k in cfg["titulo"]):
                regs.add(r)
        if not regs and not otras and ("multi" in t or "all regions" in t or "global" in t):
            regs = {"global"}
        return regs, otras

    estados = {r: [] for r in REGIONES}
    incidentes, otros = [], []

    for inc in datos.get("incidents") or []:
        if inc.get("status") in ("resolved", "postmortem"):
            continue
        regs, otras = regiones_de(inc)
        sev = IMPACTO.get(inc.get("impact"), "aviso")
        fila = {"t": inc.get("name"), "fuente": "Genesys Cloud",
                "estado": INC_TXT.get(inc.get("status"), inc.get("status")),
                "desde": iso(leer_iso(inc.get("started_at") or inc.get("created_at"))),
                "act": iso(leer_iso(inc.get("updated_at"))),
                "url": inc.get("shortlink") or GC_URL, "sev": sev}
        if regs:
            fila["region"] = "global" if regs == {"global"} else ", ".join(sorted(regs))
            incidentes.append(fila)
            for r in (REGIONES if "global" in regs else regs):
                estados[r].append(sev)
        else:
            fila["region"] = ", ".join(sorted(o for o in otras if o)) or "otra región"
            otros.append(fila)

    for m in datos.get("scheduled_maintenances") or []:
        if m.get("status") in ("completed",):
            continue
        desde, hasta = leer_iso(m.get("scheduled_for")), leer_iso(m.get("scheduled_until"))
        en_curso = m.get("status") in ("in_progress", "verifying")
        pronto = desde is not None and desde <= now + timedelta(hours=24) and (hasta is None or hasta >= now)
        if not (en_curso or pronto):
            continue
        regs, _ = regiones_de(m)
        regs = set(REGIONES) if "global" in regs else regs
        if not regs:
            continue
        fila = {"t": m.get("name"), "fuente": "Genesys Cloud", "region": ", ".join(sorted(regs)),
                "estado": f"{INC_TXT.get(m.get('status'), m.get('status'))}: "
                          f"{hora_chile(desde)} a {hora_chile(hasta)} (hora Chile)",
                "desde": iso(desde), "act": iso(leer_iso(m.get("updated_at"))),
                "url": m.get("shortlink") or GC_URL, "tipo": "mant", "sev": "mant"}
        incidentes.append(fila)
        for r in regs:
            estados[r].append("mant")

    salida = {}
    for r in REGIONES:
        hijos = [c for c in comps if not c.get("group") and region_comp.get(c["id"]) == r]
        if not hijos:
            salida[r] = dict(sin[r], nota="No se encontró la región en la página de estado")
            continue
        malos = [c for c in hijos if c.get("status") != "operational"]
        est = peor([COMP_ESTADO.get(c.get("status"), "aviso") for c in hijos] + estados[r])
        if malos:
            nota = "; ".join(f"{etiqueta[c['id']]}: {COMP_TXT.get(c.get('status'), c.get('status'))}" for c in malos)
        else:
            nota = f"{len(hijos)} de {len(hijos)} componentes operativos"
            if "mant" in estados[r]:
                nota += "; mantención programada en las próximas 24 h"
            elif estados[r]:
                nota += "; hay un incidente abierto que menciona la región"
        salida[r] = servicio(f"gc-{r}", "Genesys Cloud", nombres[r], r, est, nota,
                             [etiqueta[c["id"]] for c in malos], GC_URL)
    # Si no se encontró ninguna de nuestras regiones, se trata como fuente caída (avisa).
    ok = all(s["estado"] != "sin_dato" for s in salida.values())
    return salida, incidentes, otros[:5], ok


# ---------------------------------------------------------------- AWS
def sev_aws(titulo):
    t = titulo.lower()
    if "service disruption" in t:
        return "caida"
    if "informational" in t:
        return "aviso"
    return "degradado"


def revisar_aws(texto, now):
    nombres = {"use1": "AWS us-east-1 (N. Virginia)", "sae1": "AWS sa-east-1 (São Paulo)"}
    if not texto:
        return ({r: servicio(f"aws-{r}", "AWS", nombres[r], r, "sin_dato",
                             "No se pudo consultar la fuente", [], AWS_URL) for r in REGIONES}, [], [])
    try:
        raiz = ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError:
        return revisar_aws(None, now)

    ultimos = {}
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        guid = (it.findtext("guid") or "").strip()
        try:
            fecha = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        m = re.search(r"#([^#]+?)_\d+$", guid)
        clave = m.group(1) if m else guid
        rm = re.search(r"([a-z]{2}(?:-gov)?-[a-z]+-\d)$", clave)
        region = rm.group(1) if rm else None
        serv = clave[: -(len(region) + 1)] if region else clave
        previo = ultimos.get(clave)
        if previo is None or fecha > previo["fecha"]:
            ultimos[clave] = {"t": titulo, "fecha": fecha, "region": region, "serv": serv,
                              "link": (it.findtext("link") or "https://status.aws.amazon.com/").strip()}

    aws_reg = {cfg["aws"]: r for r, cfg in REGIONES.items()}
    por_region = {r: [] for r in REGIONES}
    incidentes, otros = [], []
    for ev in sorted(ultimos.values(), key=lambda e: e["fecha"], reverse=True):
        t = ev["t"].lower()
        if "[resolved]" in t or "operating normally" in t:
            continue
        fila = {"t": f"{ev['t']} ({ev['serv']}{' ' + ev['region'] if ev['region'] else ''})",
                "fuente": "AWS", "estado": "Abierto, sin mensaje de resolución", "desde": None,
                "act": iso(ev["fecha"]), "url": ev["link"], "sev": sev_aws(ev["t"])}
        reciente = now - ev["fecha"] <= AWS_VENTANA
        if ev["region"] in aws_reg and reciente:
            r = aws_reg[ev["region"]]
            fila["region"] = r
            incidentes.append(fila)
            por_region[r].append(fila)
        elif ev["region"] is None and reciente:
            fila["region"] = "global"
            incidentes.append(fila)
            for r in REGIONES:
                por_region[r].append(fila)
        elif ev["region"] not in aws_reg and now - ev["fecha"] <= OTROS_VENTANA:
            fila["region"] = ev["region"] or "global"
            otros.append(fila)

    salida = {}
    for r in REGIONES:
        evs = por_region[r]
        if evs:
            est = peor([e["sev"] for e in evs])
            nota = "; ".join(e["t"] for e in evs)
        else:
            est, nota = "ok", "Sin eventos abiertos en el feed de AWS"
        salida[r] = servicio(f"aws-{r}", "AWS", nombres[r], r, est, nota, [e["t"] for e in evs], AWS_URL)
    return salida, incidentes, otros[:5]


# ---------------------------------------------------------------- Microsoft
def revisar_azure(texto):
    nota_365 = ("Microsoft no publica este estado en un feed abierto. "
                "El detalle está en el centro de administración de Microsoft 365.")
    m365 = servicio("ms-365", "Microsoft", "Microsoft 365 / Teams", "global", "sin_dato", nota_365, [], M365_URL)
    if texto is None:
        return (servicio("ms-azure", "Microsoft", "Azure y Entra ID (inicio de sesión)", "global", "sin_dato",
                         "No se pudo consultar la fuente", [], AZ_URL), m365, [], [])
    try:
        raiz = ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError:
        return revisar_azure(None)
    items, incidentes, otros, m365_items = [], [], [], []
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        texto_n = norm(titulo + " " + desc)
        relevante = any(k in texto_n for k in AZ_CLAVE)
        try:
            fecha = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            fecha = None
        fila = {"t": titulo, "fuente": "Microsoft", "region": "global", "estado": "Activo en el feed de Azure",
                "desde": iso(fecha), "act": iso(fecha),
                "url": (it.findtext("link") or AZ_URL).strip(), "sev": "degradado"}
        if not relevante:  # incidente de Azure en otra región o servicio: se muestra, pero no sube el nivel
            otros.append(dict(fila, region="otra región", sev="aviso"))
            continue
        items.append(fila)
        incidentes.append(fila)
        if any(k in texto_n for k in AZ_M365):
            m365_items.append(titulo)
    if items:
        azure = servicio("ms-azure", "Microsoft", "Azure y Entra ID (inicio de sesión)", "global",
                         peor([i["sev"] for i in items]), "; ".join(i["t"] for i in items),
                         [i["t"] for i in items], AZ_URL)
    else:
        azure = servicio("ms-azure", "Microsoft", "Azure y Entra ID (inicio de sesión)", "global", "ok",
                         "Sin incidentes en el feed público de Azure", [], AZ_URL)
    if m365_items:
        m365 = dict(m365, estado="aviso", nota="El feed de Azure menciona: " + "; ".join(m365_items),
                    afectados=m365_items)
    return azure, m365, incidentes, otros


# ---------------------------------------------------------------- Telegram
def telegram(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    print("----- AVISO -----\n" + texto + "\n-----------------")
    if os.environ.get("VIGIA_SIMULAR_TELEGRAM"):  # solo para pruebas
        return True
    if not token or not chat:
        print("[aviso] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID: no se envió.", file=sys.stderr)
        return False
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto[:4000],
                                    "disable_web_page_preview": "true"}).encode()
    for intento in range(2):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                                        data=datos, timeout=20) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except Exception as e:  # noqa: BLE001
            print(f"[error] Telegram falló (intento {intento + 1}): {e}", file=sys.stderr)
            time.sleep(3)
    return False


def url_panel():
    if os.environ.get("PANEL_URL"):
        return os.environ["PANEL_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY")  # "usuario/repo"
    if repo and "/" in repo:
        dueno, nombre = repo.split("/", 1)
        if nombre.lower() == f"{dueno.lower()}.github.io":
            return f"https://{nombre}/"
        return f"https://{dueno}.github.io/{nombre}/"
    return None


# ---------------------------------------------------------------- principal
def clave(i):
    return (i.get("fuente"), i.get("t"), i.get("url"))


def armar_mensaje(tipo, estado, nuevos):
    servs = [s for s in estado["servicios"] if s["estado"] not in ("ok", "sin_dato")]
    lineas = []
    if tipo == "problema":
        lineas.append(f"[{NIVEL_TXT[estado['nivel']]}] {estado['titular']}")
    elif tipo == "recuperacion":
        lineas.append(f"[Recuperado] Se normalizó el servicio. Estado actual: {estado['titular']}")
    elif tipo == "vuelve":
        lineas.append(f"[Fuentes OK] Genesys Cloud vuelve a responder. {estado['titular']}")
    elif tipo == "mant":
        lineas.append(f"[Mantención] {estado['titular']}")
    elif tipo == "sin_dato":
        lineas.append("[Sin datos] No se pudo consultar la página de estado de Genesys Cloud")
    lineas.append(estado["detalle"])
    for s in servs:
        lineas.append(f"• {s['n']}: {NIVEL_TXT[s['estado']]} — {s['nota']}")
    incs = estado["incidentes"]
    if incs:
        lineas.append("")
        for i in incs[:6]:
            marca = " (NUEVO)" if clave(i) in nuevos else ""
            desde = (f", desde {hora_chile(leer_iso(i['desde']))}"
                     if i.get("desde") and i.get("tipo") != "mant" else "")
            lineas.append(f"– {i['t']}{marca} [{i['region']}] {i['estado']}{desde}\n  {i['url']}")
    lineas.append("")
    lineas.append(f"Revisado {hora_chile(leer_iso(estado['revisado']))} (hora Chile)")
    panel = url_panel()
    if panel:
        lineas.append(f"Panel: {panel}")
    return "\n".join(lineas)


def main():
    now = ahora()
    try:
        with open(RUTA_ESTADO, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}

    if os.environ.get("VIGIA_PRUEBA", "").lower() in ("1", "true", "si", "sí", "yes"):
        telegram("🧪 PRUEBA del vigía de continuidad: si ves esto, los avisos por Telegram funcionan.\n"
                 "A continuación, un EJEMPLO de cómo se ve un aviso de caída (datos inventados, no es real)."
                 + (f"\nPanel: {url_panel()}" if url_panel() else ""))
        ej_srv = servicio("gc-sae1", "Genesys Cloud", "Genesys Cloud · Americas (São Paulo)", "sae1", "degradado",
                          "Inbound Calls: Partial Outage", ["Inbound Calls"], GC_URL)
        ejemplo = {"nivel": "degradado", "titular": "Degradación de Inbound Calls en sae1",
                   "detalle": "EJEMPLO DE PRUEBA. Genesys sae1: Inbound Calls: Partial Outage. Genesys use1, AWS y Azure sin problemas.",
                   "servicios": [ej_srv],
                   "incidentes": [{"t": "Elevated error rates on inbound calls - Americas (São Paulo)",
                                   "fuente": "Genesys Cloud", "region": "sae1", "estado": "Investigando",
                                   "desde": iso(now - timedelta(minutes=12)), "act": iso(now), "url": GC_URL}],
                   "revisado": iso(now)}
        telegram("🧪 EJEMPLO (no es real)\n" + armar_mensaje("problema", ejemplo,
                                                            {clave(ejemplo["incidentes"][0])}))

    gc, gc_inc, gc_otros, gc_ok = revisar_genesys(bajar(GC_API), now)
    aws, aws_inc, aws_otros = revisar_aws(bajar(AWS_RSS), now)
    azure, m365, az_inc, az_otros = revisar_azure(bajar(AZ_RSS))

    # Si una fuente no respondió, se conservan sus incidentes anteriores para no
    # perderlos (y no avisarlos como "nuevos" cuando vuelva).
    def conservar(fuente, lista, clave_lista):
        return lista + [i for i in prev.get(clave_lista) or [] if i.get("fuente") == fuente]
    if not gc_ok:
        gc_inc = conservar("Genesys Cloud", gc_inc, "incidentes")
        gc_otros = conservar("Genesys Cloud", gc_otros, "otros")
    if aws["use1"]["estado"] == "sin_dato":
        aws_inc = conservar("AWS", aws_inc, "incidentes")
        aws_otros = conservar("AWS", aws_otros, "otros")
    if azure["estado"] == "sin_dato":
        az_inc = conservar("Microsoft", az_inc, "incidentes")
    servicios = [gc["use1"], aws["use1"], gc["sae1"], aws["sae1"], azure, m365]
    cuentan = [gc["use1"], aws["use1"], gc["sae1"], aws["sae1"], azure]
    if not gc_ok:
        nivel = "sin_dato"
    else:
        nivel = peor([s["estado"] for s in cuentan])

    incidentes = gc_inc + aws_inc + az_inc
    otros = (gc_otros + aws_otros + az_otros)[:5]

    # titular y detalle
    if nivel == "sin_dato":
        titular = "Sin datos de Genesys Cloud"
    elif nivel == "ok":
        titular = "Todo operativo en use1 y sae1"
    else:
        s = max(cuentan, key=lambda x: ORDEN.index(x["estado"]) if x["estado"] in ORDEN else -1)
        palabra = {"caida": "Caída", "degradado": "Degradación", "aviso": "Aviso", "mant": "Mantención"}[nivel]
        if s["afectados"] and s["grupo"] == "Genesys Cloud":
            titular = f"{palabra} de {', '.join(s['afectados'][:2])} en {s['region']}"
        else:
            titular = f"{palabra} en {s['n']}"
    partes = []
    for r in REGIONES:
        partes.append(f"Genesys {r}: {gc[r]['nota']}")
    partes.append(f"AWS: {aws['use1']['nota'] if aws['use1']['estado'] != 'ok' else 'sin eventos en us-east-1'}"
                  f" / {aws['sae1']['nota'] if aws['sae1']['estado'] != 'ok' else 'sin eventos en sa-east-1'}")
    partes.append(f"Azure: {azure['nota']}")
    detalle = ". ".join(partes) + "."

    estado = {
        "revisado": iso(now), "intervalo": "cada 5 minutos", "nivel": nivel, "titular": titular, "detalle": detalle,
        "servicios": servicios,
        "incidentes": [{k: v for k, v in i.items() if k != "sev"} for i in incidentes],
        "otros": [{k: v for k, v in i.items() if k != "sev"} for i in otros],
        "historial": ((prev.get("historial") or []) + [{"at": iso(now), "nivel": nivel}])[-HISTORIAL_MAX:],
        "ultimo_aviso": prev.get("ultimo_aviso"),
    }

    # ¿avisamos?
    nivel_prev = prev.get("nivel")
    claves_prev = {clave(i) for i in prev.get("incidentes") or []}
    nuevos = {clave(i) for i in estado["incidentes"]} - claves_prev
    nuevos_problema = [i for i in estado["incidentes"] if clave(i) in nuevos and i.get("tipo") != "mant"]
    nuevas_mant = [i for i in estado["incidentes"] if clave(i) in nuevos and i.get("tipo") == "mant"]
    ultimo = leer_iso(prev.get("ultimo_aviso"))
    vencido = ultimo is None or now - ultimo >= REAVISO

    tipo = None
    if nivel in ("degradado", "caida") and (nivel != nivel_prev or nuevos_problema or vencido):
        tipo = "problema"
    elif nivel_prev in ("degradado", "caida") and nivel in ("ok", "mant", "aviso"):
        tipo = "recuperacion"
    elif nivel_prev == "sin_dato" and nivel != "sin_dato" and prev:
        tipo = "vuelve"
    elif nuevas_mant:
        tipo = "mant"
    elif nivel == "sin_dato" and (nivel_prev != "sin_dato" or vencido):
        tipo = "sin_dato"

    avisado = False
    if tipo:
        avisado = telegram(armar_mensaje(tipo, estado, nuevos))
        if avisado:
            estado["ultimo_aviso"] = iso(now)

    os.makedirs(os.path.dirname(RUTA_ESTADO) or ".", exist_ok=True)
    with open(RUTA_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)

    cambio = "sin cambios" if nivel == nivel_prev else f"{nivel_prev} → {nivel}"
    print(f"Nivel: {nivel} ({cambio}). {titular}. "
          f"Aviso: {'enviado (' + tipo + ')' if avisado else ('falló o sin Telegram (' + tipo + ')' if tipo else 'no')}")


if __name__ == "__main__":
    main()
