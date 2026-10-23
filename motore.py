"""
motore.py — la parte che usa il browser (Microsoft Edge via Playwright).
"""
import datetime as dt
import hashlib
import json
import os
import random
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import logica as L

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0")
SEL_CLICK = "a, button, [role=link], [role=menuitem], [role=button], [onclick]"
COOKIE_TESTI = ["accetta tutti", "accetto tutti", "accetta", "accetto", "acconsento", "ho capito", "ok", "chiudi"]
MAX_PAGINE = 40
PAUSA_STESSO_SITO = 4   # secondi minimi tra due albi sullo stesso dominio

JS_RIGHE = r"""() => {
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const links = e => Array.from(e.querySelectorAll('a[href]')).map(a => ({href: a.href, text: txt(a) || a.title || ''}));
  const out = [];
  window.__albiN = window.__albiN || 0;
  const id = e => { if (!e.dataset.albiId) e.dataset.albiId = String(++window.__albiN); return e.dataset.albiId; };
  for (const t of document.querySelectorAll('table')) {
    let headers = Array.from(t.querySelectorAll('thead th, thead td')).map(txt);
    let rows = Array.from(t.querySelectorAll('tbody tr'));
    if (!rows.length) rows = Array.from(t.querySelectorAll('tr'));
    if (!headers.length && rows.length && rows[0].querySelectorAll('th').length) {
      headers = Array.from(rows[0].querySelectorAll('th')).map(txt); rows = rows.slice(1);
    }
    for (const r of rows) {
      if (r.querySelector('table')) continue;
      const cells = Array.from(r.querySelectorAll(':scope > td')).map(txt);
      if (cells.length < 2) continue;
      out.push({headers, cells, text: txt(r), links: links(r), id: id(r)});
    }
  }
  if (out.length < 2) {
    const sel = 'article, .card, li, .list-group-item, .item, [class*="atto"], [class*="pubblicazion"], [class*="documento"]';
    for (const c of document.querySelectorAll(sel)) {
      const t = txt(c);
      if (t.length < 20 || t.length > 3000) continue;
      if (!/\d{1,2}[\/.\-]\d{1,2}[\/.\-]\d{2,4}/.test(t)) continue;
      if (c.querySelector(sel)) continue;
      out.push({headers: [], cells: [], text: t, links: links(c), id: id(c)});
    }
  }
  return out;
}"""

JS_SUCCESSIVA = r"""(sel) => {
  const els = Array.from(document.querySelectorAll(sel));
  const re = /^(successiv[ao]|pagina successiva|avanti|next|›|»|>|>>|→)$/i;
  for (let i = 0; i < els.length; i++) {
    const e = els[i];
    const t = (e.innerText || '').trim();
    const al = (e.getAttribute('aria-label') || '') + ' ' + (e.title || '');
    if (re.test(t) || /successiv|next|pagina seguente/i.test(al) || e.getAttribute('rel') === 'next') {
      const li = e.closest('li');
      if (e.disabled || e.getAttribute('aria-disabled') === 'true' || (li && li.classList.contains('disabled'))) continue;
      const r = e.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      return i;
    }
  }
  return -1;
}"""

JS_CANDIDATI = """(sel) => Array.from(document.querySelectorAll(sel)).map((e, i) => ({
    i, text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 200),
    title: e.getAttribute('title') || '', href: e.href || e.getAttribute('href') || ''
}))"""


# pagina tuttitalia con l'elenco delle province di ogni regione (con le sigle)
PROVINCE_REGIONE = {
    "abruzzo": "https://www.tuttitalia.it/abruzzo/42-province/numero-comuni/",
    "basilicata": "https://www.tuttitalia.it/basilicata/81-province/numero-comuni/",
    "calabria": "https://www.tuttitalia.it/calabria/65-province/numero-comuni/",
    "campania": "https://www.tuttitalia.it/campania/96-province/numero-comuni/",
    "emilia-romagna": "https://www.tuttitalia.it/emilia-romagna/89-province/numero-comuni/",
    "friuli-venezia-giulia": "https://www.tuttitalia.it/friuli-venezia-giulia/79-province/numero-comuni/",
    "lazio": "https://www.tuttitalia.it/lazio/27-province/numero-comuni/",
    "liguria": "https://www.tuttitalia.it/liguria/98-province/numero-comuni/",
    "lombardia": "https://www.tuttitalia.it/lombardia/20-province/numero-comuni/",
    "marche": "https://www.tuttitalia.it/marche/36-province/numero-comuni/",
    "molise": "https://www.tuttitalia.it/molise/97-province/numero-comuni/",
    "piemonte": "https://www.tuttitalia.it/piemonte/46-province/numero-comuni/",
    "puglia": "https://www.tuttitalia.it/puglia/74-province/numero-comuni/",
    "sardegna": "https://www.tuttitalia.it/sardegna/97-province/numero-comuni/",
    "sicilia": "https://www.tuttitalia.it/sicilia/26-province/numero-comuni/",
    "toscana": "https://www.tuttitalia.it/toscana/19-province/numero-comuni/",
    "trentino-alto-adige": "https://www.tuttitalia.it/trentino-alto-adige/77-province/numero-comuni/",
    "umbria": "https://www.tuttitalia.it/umbria/19-province/numero-comuni/",
    "valle-d-aosta": "https://www.tuttitalia.it/valle-d-aosta/35-province/numero-comuni/",
    "veneto": "https://www.tuttitalia.it/veneto/38-province/numero-comuni/",
}

JS_PROVINCE = r"""(reg) => {
  const out = [];
  for (const tr of document.querySelectorAll('table tr')) {
    const a = Array.from(tr.querySelectorAll('a[href]')).find(a => new RegExp('tuttitalia\\.it/' + reg + '/[^/]+/$').test(a.href));
    if (!a) continue;
    const m = (tr.innerText || '').match(/\b([A-Z]{2})\b/);
    out.push({nome: a.innerText.replace(/\s+/g, ' ').trim(), href: a.href, sigla: m ? m[1] : ''});
  }
  return out;
}"""


# Ambito di una riga: la riga stessa più le righe "di dettaglio" subito sotto (tendine di tabelle)
JS_AMBITO = r"""
const __albiAmbito = (id) => {
  const el = document.querySelector('[data-albi-id="' + id + '"]');
  if (!el) return [];
  const out = [el];
  if (el.tagName === 'TR') {
    const nc = el.children.length;
    let s = el.nextElementSibling, k = 0;
    while (s && k < 4 && s.tagName === 'TR' && !s.dataset.albiId &&
           (s.children.length < Math.max(2, nc / 2) || s.querySelector('td[colspan]') || /detail|dettagl|child|collapse|espans/i.test(s.className || ''))) {
      out.push(s); s = s.nextElementSibling; k++;
    }
  }
  return out;
};
"""

# Segna i comandi che aprono tendine/dettagli nella riga (senza toccare i link che portano altrove)
JS_SEGNA_TENDINE = "(id) => {" + JS_AMBITO + r"""
  const amb = __albiAmbito(id);
  if (!amb.length) return null;
  document.querySelectorAll('[data-albi-toggle]').forEach(e => e.removeAttribute('data-albi-toggle'));
  const targets = []; let n = 0;
  const sel = 'a, button, [role=button], [data-toggle], [data-bs-toggle], [aria-expanded], summary, [onclick], .details-control, td.dt-control';
  for (const sc of amb) for (const c of sc.querySelectorAll(sel)) {
    if (n >= 6) break;
    const href = c.getAttribute('href') || '';
    if (c.tagName === 'A' && href && !href.startsWith('#') && !/^javascript:/i.test(href)) continue;
    const cls = typeof c.className === 'string' ? c.className : '';
    const testo = ((c.innerText || '') + ' ' + (c.title || '') + ' ' + (c.getAttribute('aria-label') || '') + ' ' + cls).toLowerCase();
    const soloSimbolo = /^[\s+▸▶▼▾›»>⌄˅…]*$/.test((c.innerText || '').trim());
    const tendina = c.getAttribute('aria-expanded') === 'false' || c.hasAttribute('data-toggle') || c.hasAttribute('data-bs-toggle')
      || c.tagName === 'SUMMARY' || /details-control|dt-control|toggle|expand|collaps|accordion/.test(cls)
      || /dettagl|allegat|espandi|mostra|visualizza|document|apri|plus|chevron|caret|freccia/.test(testo) || soloSimbolo;
    if (!tendina) continue;
    c.setAttribute('data-albi-toggle', String(n++));
    const tg = c.getAttribute('aria-controls') || c.getAttribute('data-target') || c.getAttribute('data-bs-target') || (href.length > 1 && href.startsWith('#') ? href : '');
    if (tg) tg.split(/\s+/).forEach(t => targets.push(t.startsWith('#') ? t : '#' + t));
  }
  return {n, targets};
}
"""

# Raccoglie i link nell'ambito della riga, nei pannelli aperti e nelle finestre visibili
JS_LINK_AMBITO = "([id, targets]) => {" + JS_AMBITO + r"""
  const out = [];
  const add = root => { if (root && root.querySelectorAll) root.querySelectorAll('a[href]').forEach(a => out.push({href: a.href, text: (a.innerText || a.title || '').trim()})); };
  __albiAmbito(id).forEach(add);
  for (const t of targets || []) { try { document.querySelectorAll(t).forEach(add); } catch (e) {} }
  document.querySelectorAll('.modal.show, .modal.in, [role=dialog], dialog[open], .ui-dialog, .popup, .featherlight').forEach(m => {
    const r = m.getBoundingClientRect(); if (r.width > 0 && r.height > 0) add(m);
  });
  return out;
}
"""

# Comandi che scaricano un file solo cliccando (nessun link vero), nell'ambito della riga o nelle finestre aperte
JS_SEGNA_DOWNLOAD = "(id) => {" + JS_AMBITO + r"""
  document.querySelectorAll('[data-albi-dl]').forEach(e => e.removeAttribute('data-albi-dl'));
  const radici = __albiAmbito(id);
  document.querySelectorAll('.modal.show, .modal.in, [role=dialog], dialog[open], .ui-dialog').forEach(m => {
    const r = m.getBoundingClientRect(); if (r.width > 0 && r.height > 0) radici.push(m); });
  let n = 0;
  for (const rt of radici) for (const c of rt.querySelectorAll('a, button, [role=button], [onclick], input[type=button], input[type=submit]')) {
    if (n >= 12) break;
    const href = c.getAttribute('href') || '';
    const vero = c.tagName === 'A' && href && !href.startsWith('#') && !/^javascript:/i.test(href);
    if (vero) continue;
    const t = ((c.innerText || c.value || '') + ' ' + (c.title || '') + ' ' + (c.getAttribute('aria-label') || '')).toLowerCase();
    if (/\.(pdf|p7m|docx?|odt|zip|xlsx?|rtf|tiff?|jpe?g)\b|scarica|download|allegat/.test(t)) c.setAttribute('data-albi-dl', String(n++));
  }
  return n;
}
"""

JS_HTML_AMBITO = "(id) => {" + JS_AMBITO + r"""
  return __albiAmbito(id).map(e => e.outerHTML).join("\n").slice(0, 15000);
}"""

# elenco degli elementi cliccabili (in una riga, o nell'intera pagina se id è null)
JS_CLICCABILI = "(id) => {" + JS_AMBITO + r"""
  const radici = id ? __albiAmbito(id) : [document.body];
  const out = [];
  for (const r of radici) for (const c of r.querySelectorAll('a, button, input[type=button], input[type=submit], input[type=image], [onclick], [role=button], [data-toggle], [data-bs-toggle], td.details-control, td.dt-control')) {
    if (out.length >= 80) break;
    out.push({tag: c.tagName, testo: (c.innerText || c.value || c.alt || '').trim().slice(0, 80), title: c.title || '',
              classe: (typeof c.className === 'string' ? c.className : '').slice(0, 60), href: (c.getAttribute('href') || '').slice(0, 200),
              onclick: (c.getAttribute('onclick') || '').slice(0, 200), visibile: !!(c.offsetWidth || c.offsetHeight)});
  }
  return out;
}"""

JS_TUTTI_LINK = r"""() => {
  const out = [];
  const testo = e => ((e.innerText || '').trim() || e.title || e.getAttribute('aria-label') || '').trim();
  for (const a of document.querySelectorAll('a[href]')) out.push({href: a.href, text: testo(a)});
  // indirizzi nascosti nel codice del clic: window.open(atob('...')), location.href = '...'
  for (const e of document.querySelectorAll('[onclick]')) {
    const oc = e.getAttribute('onclick') || '', t = testo(e) || 'download';
    const add = u => { try { if (u && !/^javascript:/i.test(u)) out.push({href: new URL(u, location.href).href, text: t, daClic: true}); } catch (x) {} };
    let m;
    const r1 = /atob\(\s*['"]([A-Za-z0-9+\/=]+)['"]\s*\)/g;
    while ((m = r1.exec(oc))) { try { add(atob(m[1])); } catch (x) {} }
    const r2 = /(?:window\.open|location\.assign|location\.replace)\s*\(\s*['"]([^'"]+)['"]/g;
    while ((m = r2.exec(oc))) add(m[1]);
    const r3 = /location(?:\.href)?\s*=\s*['"]([^'"]+)['"]/g;
    while ((m = r3.exec(oc))) add(m[1]);
  }
  return out;
}"""

# comandi "scarica" in tutta la pagina (dettaglio): pulsanti senza link vero
JS_SEGNA_DOWNLOAD_PAGINA = r"""() => {
  document.querySelectorAll('[data-albi-dlp]').forEach(e => e.removeAttribute('data-albi-dlp'));
  let n = 0;
  for (const c of document.querySelectorAll('a, button, [role=button], [onclick], input[type=button], input[type=submit]')) {
    if (n >= 12) break;
    const href = c.getAttribute('href') || '';
    if (c.tagName === 'A' && href && !href.startsWith('#') && !/^javascript:/i.test(href)) continue;
    const t = ((c.innerText || c.value || '') + ' ' + (c.title || '') + ' ' + (c.getAttribute('aria-label') || '')).toLowerCase();
    if (/torna|copia|stampa la pagina|cerca|accedi|chiudi/.test(t)) continue;
    if (/\.(pdf|p7m|docx?|odt|zip|xlsx?|rtf|tiff?|jpe?g)\b|scarica|download|allegat|documento principale|visualizza documento/.test(t)) {
      const r = c.getBoundingClientRect(); if (r.width === 0 && r.height === 0) continue;
      c.setAttribute('data-albi-dlp', String(n++));
    }
  }
  return n;
}"""

# attende che la pagina abbia finito di caricare (rotelline di caricamento sparite, testo presente)
JS_PRONTA = r"""() => {
  if (document.readyState !== 'complete') return false;
  const giro = Array.from(document.querySelectorAll('[class*=spinner], [class*=loader], [class*=loading], [id*=loading], [id*=spinner], .blockUI'))
    .some(e => { const r = e.getBoundingClientRect(); const st = getComputedStyle(e); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0'; });
  return !giro && document.body && document.body.innerText.trim().length > 200;
}"""


class Annullato(Exception):
    pass


class Motore:
    def __init__(self, log, avanzamento, stop, visibile=False):
        self.log = log                  # funzione(testo)
        self.avanzamento = avanzamento  # funzione(fatti, totale, testo)
        self.stop = stop                # threading.Event
        self.visibile = visibile

    # ------------------------------------------------------ browser

    def _controlla_stop(self):
        if self.stop.is_set():
            raise Annullato()

    def _avvia(self, p):
        for canale in ("msedge", "chrome", None):
            try:
                kw = {"headless": not self.visibile}
                if canale:
                    kw["channel"] = canale
                b = p.chromium.launch(**kw)
                self.log(f"Browser: {canale or 'Chromium'}")
                return b
            except Exception:
                continue
        raise RuntimeError("Nessun browser trovato. Serve Microsoft Edge o Google Chrome installato.")

    def _contesto(self, browser):
        ctx = browser.new_context(locale="it-IT", user_agent=UA, viewport={"width": 1366, "height": 900},
                                  accept_downloads=True, ignore_https_errors=True)
        ctx.set_default_timeout(15000)
        return ctx

    def _chiudi_cookie(self, page):
        for t in COOKIE_TESTI:
            try:
                btn = page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(t)}\b", re.I)).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=2000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                pass

    def _pausa_cortesia(self, url):
        """Non bombarda lo stesso sito: tra due albi sullo stesso dominio lascia qualche secondo."""
        if not hasattr(self, "_ultimo_accesso"):
            self._ultimo_accesso = {}
        host = urlparse(url).hostname or ""
        base = ".".join(host.split(".")[-2:])
        trascorso = time.time() - self._ultimo_accesso.get(base, 0)
        attesa = PAUSA_STESSO_SITO + random.uniform(0, 2)
        if trascorso < attesa:
            time.sleep(attesa - trascorso)
        self._ultimo_accesso[base] = time.time()

    def _apri(self, page, url, attesa=1500, cortese=False):
        if cortese:
            self._pausa_cortesia(url)
        self.ultimo_stato = None
        for tentativo in (1, 2):
            try:
                risp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.ultimo_stato = risp.status if risp else None
                break
            except PWTimeout:
                if tentativo == 2:
                    raise
                self.log("  pagina lenta, riprovo…")
                page.wait_for_timeout(5000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass
        page.wait_for_timeout(attesa)
        self._chiudi_cookie(page)

    def _clicca_e_segui(self, page, frame, sel, indice):
        el = frame.locator(sel).nth(indice)
        prima = page.url
        try:
            with page.context.expect_page(timeout=5000) as nuova:
                el.click(timeout=5000)
            p2 = nuova.value
            try:
                p2.wait_for_load_state("domcontentloaded", timeout=15000)
            except PWTimeout:
                pass
            url = p2.url
            p2.close()
            return url
        except Exception:
            page.wait_for_timeout(2500)
            return page.url if page.url != prima else ""

    # ================================================ FASE 1: trova albi

    def trova_albi(self, url_elenco, comuni_esistenti):
        manuali = {c["nome"]: c for c in comuni_esistenti if c.get("manuale")}
        risultati = []
        with sync_playwright() as p:
            browser = self._avvia(p)
            ctx = self._contesto(browser)
            page = ctx.new_page()
            self.log(f"Leggo l'elenco dei comuni: {url_elenco}")
            comuni = self._leggi_elenco(page, url_elenco)
            self.log(f"Trovati {len(comuni)} comuni")
            for n, c in enumerate(comuni, 1):
                self._controlla_stop()
                self.avanzamento(n - 1, len(comuni), c["nome"])
                if c["nome"] in manuali:
                    risultati.append(manuali[c["nome"]])
                    self.log(f"{c['nome']}: link inserito a mano, lo tengo")
                    continue
                try:
                    c["sito"] = self._sito_istituzionale(page, c["scheda"])
                    if not c["sito"]:
                        c.update(albo="", altri=[], piattaforma="", note="sito non trovato su tuttitalia")
                    else:
                        c.update(self._trova_albo(page, c["sito"]))
                    self.log(f"{c['nome']}: {c['albo'] or 'NON TROVATO – ' + c['note']}")
                except Annullato:
                    raise
                except Exception as e:
                    c.update(albo=c.get("albo", ""), altri=[], piattaforma="", note=f"errore: {str(e)[:100]}")
                    self.log(f"{c['nome']}: errore ({type(e).__name__})")
                    page = self._pagina_nuova(ctx, page)
                risultati.append(c)
            self.avanzamento(len(comuni), len(comuni), "Fatto")
            browser.close()
        return risultati

    def trova_albi_selezione(self, sel, esistenti, salva_parziale=None):
        """
        sel: {"regione": "Veneto", "sigla": "BL" o "", "comuni": [nomi] o [], "salta_trovati": bool}
        esistenti: comuni già in archivio (per tenere i link inseriti a mano e saltare quelli già trovati)
        Ritorna la lista dei comuni elaborati, con regione/provincia/sigla.
        """
        per_chiave = {L.chiave(c): c for c in esistenti}
        reg_slug = L.slug(sel["regione"].split("/")[0])
        url_prov = PROVINCE_REGIONE.get(reg_slug)
        if not url_prov:
            raise RuntimeError(f"Regione non riconosciuta: {sel['regione']}")
        risultati = []
        with sync_playwright() as p:
            browser = self._avvia(p)
            ctx = self._contesto(browser)
            page = ctx.new_page()

            self.log(f"Leggo le province di {sel['regione']}")
            self._apri(page, url_prov, attesa=500)
            province = page.evaluate(JS_PROVINCE, reg_slug)
            if sel.get("sigla"):
                province = [x for x in province if x["sigla"] == sel["sigla"]]
            if not province:
                raise RuntimeError("Provincia non trovata su tuttitalia: " + (sel.get("sigla") or sel["regione"]))

            da_fare = []
            voluti = {L.norm(n) for n in sel.get("comuni") or []}
            for pr in province:
                self._controlla_stop()
                elenco = self._leggi_elenco(page, pr["href"])
                nome_prov = re.sub(r"(?i)^(provincia( autonoma)?|citt[aà] metropolitana|libero consorzio( comunale)?) d[i'el]+\s*", "", pr["nome"]).strip()
                self.log(f"Provincia di {nome_prov} ({pr['sigla']}): {len(elenco)} comuni")
                for c in elenco:
                    c.update(regione=sel["regione"].split("/")[0], provincia=nome_prov.title() if nome_prov.isupper() else nome_prov,
                             sigla=pr["sigla"])
                    if voluti and L.norm(c["nome"]) not in voluti:
                        continue
                    da_fare.append(c)
            if voluti:
                trovati = {L.norm(c["nome"]) for c in da_fare}
                for n in sel.get("comuni"):
                    if L.norm(n) not in trovati:
                        self.log(f"{n}: non trovato nell'elenco di tuttitalia")

            for n, c in enumerate(da_fare, 1):
                self._controlla_stop()
                self.avanzamento(n - 1, len(da_fare), f"{c['nome']} ({c['sigla']})")
                vecchio = per_chiave.get(L.chiave(c))
                if vecchio and vecchio.get("manuale"):
                    risultati.append({**vecchio, **{k: c[k] for k in ("regione", "provincia", "sigla")}})
                    self.log(f"{c['nome']}: link inserito a mano, lo tengo")
                    continue
                if vecchio and vecchio.get("albo") and sel.get("salta_trovati"):
                    risultati.append({**vecchio, **{k: c[k] for k in ("regione", "provincia", "sigla")}})
                    self.log(f"{c['nome']}: già trovato, salto")
                    continue
                try:
                    c["sito"] = self._sito_istituzionale(page, c["scheda"])
                    if not c["sito"]:
                        c.update(albo="", altri=[], piattaforma="", note="sito non trovato su tuttitalia")
                    else:
                        c.update(self._trova_albo(page, c["sito"]))
                    self.log(f"{c['nome']}: {c['albo'] or 'NON TROVATO – ' + c['note']}")
                except Annullato:
                    raise
                except Exception as e:
                    c.update(albo="", altri=[], piattaforma="", note=f"errore: {str(e)[:100]}")
                    self.log(f"{c['nome']}: errore ({type(e).__name__})")
                    page = self._pagina_nuova(ctx, page)
                risultati.append(c)
                if salva_parziale and n % 5 == 0:
                    salva_parziale(risultati)
            self.avanzamento(len(da_fare), len(da_fare), "Fatto")
            browser.close()
        return risultati

    def _pagina_nuova(self, ctx, page):
        try:
            page.close()
        except Exception:
            pass
        return ctx.new_page()

    def _leggi_elenco(self, page, url):
        self._apri(page, url)
        link = page.eval_on_selector_all("table a", "els => els.map(e => ({nome: e.innerText.trim(), href: e.href}))")
        comuni, visti = [], set()
        for l in link:
            h = l["href"]
            if not re.match(r"^https://www\.tuttitalia\.it/[a-z-]+/\d+-[a-z0-9-]+/$", h):
                continue
            if re.search(r"\d+-comuni/$", h) or not l["nome"] or h in visti:
                continue
            visti.add(h)
            nome = l["nome"].title() if l["nome"].isupper() else l["nome"]
            comuni.append({"nome": nome, "scheda": h})
        return comuni

    def _sito_istituzionale(self, page, url):
        self._apri(page, url, attesa=500)
        href = page.evaluate("""() => {
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length < 3 && /Sito istituzionale/i.test(el.textContent || '')) {
                    const box = el.closest('p, li, div, td') || el.parentElement;
                    const a = box && box.querySelector('a[href^="http"]');
                    if (a && !/tuttitalia|nonsolocap/i.test(a.href)) return a.href;
                }
            }
            return '';
        }""")
        if href:
            return L.normalizza_url(href)
        m = re.search(r"Sito istituzionale\s*:?\s*(\S+)", page.inner_text("body"), re.I)
        return L.normalizza_url(m.group(1)) if m else ""

    def _candidati_albo(self, page):
        out = []
        for fi, frame in enumerate(page.frames):
            try:
                els = frame.evaluate(JS_CANDIDATI, SEL_CLICK)
            except Exception:
                continue
            for c in els:
                s = L.punteggio_link_albo(c)
                if s is None:
                    continue
                c.update(score=s, frame=fi)
                if L.href_utile(c["href"]):
                    c["href"] = urljoin(frame.url, c["href"])
                out.append(c)
        return sorted(out, key=lambda c: c["score"], reverse=True)

    def _trova_albo(self, page, sito):
        note = []
        self._apri(page, sito)
        info = page.evaluate("""() => ({
            title: document.title,
            generator: (document.querySelector('meta[name=generator]') || {}).content || '',
            credits: Array.from(document.querySelectorAll('a')).map(a => a.href)
                     .filter(h => /halley|maggioli|municipium/i.test(h)).join(' ')
        })""")
        cand = self._candidati_albo(page)
        if not cand:
            try:
                at = page.get_by_role("link", name=re.compile(r"amministrazione\s+trasparente", re.I)).first
                if at.count() and L.href_utile(at.get_attribute("href")):
                    self._apri(page, urljoin(page.url, at.get_attribute("href")))
                    cand = self._candidati_albo(page)
                    if cand:
                        note.append("trovato da Amministrazione trasparente")
            except Exception:
                pass
        if not cand:
            return {"albo": "", "altri": [], "piattaforma": L.indovina_piattaforma([], info),
                    "note": "albo non trovato in home"}

        principali = [c for c in cand if not L.e_archivio(c)] or cand
        top = principali[0]
        if L.href_utile(top["href"]) and top["href"].rstrip("/") != page.url.rstrip("/"):
            link = top["href"]
        else:
            link = self._clicca_e_segui(page, page.frames[top["frame"]], SEL_CLICK, top["i"])
            note.append("link ottenuto cliccando" if link else "voce 'Albo' presente ma il link non si risolve: verifica")
        altri = []
        for c in cand:
            if L.href_utile(c["href"]) and c["href"] != link and c["href"] not in altri:
                altri.append(c["href"])
            if len(altri) >= 3:
                break
        if any(L.e_archivio(c) for c in cand):
            note.append("presente archivio storico")
        return {"albo": link, "altri": altri, "piattaforma": L.indovina_piattaforma([link] + altri, info),
                "note": "; ".join(note)}

    # ================================== FASE 2: monitoraggio determine

    def monitora(self, comuni, dal_per_comune, opz, cartella):
        """
        comuni: lista con 'nome' e 'albo'
        dal_per_comune: funzione(comune) -> date
        opz: parole, esclusioni, solo_determine, scarica, esclusi (id di atti eliminati dall'utente)
        """
        risultati, riepilogo = [], []
        esclusi = set(opz.get("esclusi") or [])
        self._diag_dir = opz.get("diagnostica")
        da_fare = [c for c in comuni if c.get("albo")]
        with sync_playwright() as p:
            browser = self._avvia(p)
            ctx = self._contesto(browser)
            page = ctx.new_page()
            for n, c in enumerate(da_fare, 1):
                self._controlla_stop()
                nome = c["nome"]
                dal = dal_per_comune(c)
                self.avanzamento(n - 1, len(da_fare), nome)
                rie = {"comune": nome, "sigla": c.get("sigla", ""), "provincia": c.get("provincia", ""),
                       "albo": c["albo"], "dal": dal, "pagine": 0, "letti": 0, "trovati": 0, "esito": ""}
                contatore = {}
                trovati_qui = []

                def gestisci(pg, frame, riga, a, c=c, nome=nome, dal=dal, contatore=contatore, trovati_qui=trovati_qui):
                    if a["data"] and a["data"] < dal:
                        return
                    parole = L.valuta_atto(a, opz["parole"], opz["esclusioni"], opz["solo_determine"])
                    if not parole:
                        return
                    ida = L.id_atto(nome, c.get("sigla", ""), a["data"], a["numero"], a["oggetto"])
                    url_elenco = pg.url          # l'elenco vero (non il menu da cui si è partiti)
                    if ida in esclusi:
                        return
                    self._controlla_stop()
                    link = L.link_dettaglio(a)
                    if link and urldefrag(link)[0] == urldefrag(pg.url)[0]:
                        link = ""      # "link" alla pagina stessa (es. pulsante di una tendina): non è un dettaglio
                    allegati = []
                    if opz["scarica"]:
                        allegati = self._allegati_atto(pg, frame, riga, a, link, ctx, nome, cartella, contatore)
                    trovati_qui.append({"id": ida, "comune": nome, "sigla": c.get("sigla", ""), "provincia": c.get("provincia", ""),
                                        "data": a["data"], "tipo": a["tipo"], "numero": a["numero"],
                                        "oggetto": a["oggetto"], "parole": parole, "link": link or url_elenco,
                                        "allegati": allegati})

                self._diag_contati = 0
                try:
                    url_albo = c.get("albo_lista") or c["albo"]
                    atti, pagine = self._leggi_albo(page, url_albo, dal, gestisci, opz["solo_determine"])
                    if not atti and c.get("albo_lista"):      # il link "diretto" non funziona più: riparto dal link originale
                        atti, pagine = self._leggi_albo(page, c["albo"], dal, gestisci, opz["solo_determine"])
                    if self.url_lista:
                        rie["albo_lista"] = self.url_lista
                    rie.update(pagine=pagine, letti=len(atti), trovati=len(trovati_qui))
                    risultati += trovati_qui
                    senza = sum(1 for r in trovati_qui if not r["allegati"])
                    if not atti:
                        d = self._diagnosi(page, c, opz.get("diagnostica"), "nessuna riga letta")
                        rie.update(d)
                        rie["esito"] = "Nessuna riga letta: " + d["diagnosi"]
                    else:
                        rie["esito"] = f"OK – {len(trovati_qui)} trovati" + (f", {senza} senza allegati" if opz["scarica"] and senza else "")
                    self.log(f"{nome}: {len(atti)} atti letti su {pagine} pagine, {len(trovati_qui)} trovati"
                             + (f" ({senza} senza allegati)" if opz["scarica"] and senza else ""))
                except Annullato:
                    raise
                except Exception as e:
                    risultati += trovati_qui
                    d = self._diagnosi(page, c, opz.get("diagnostica"), f"{type(e).__name__}: {str(e).splitlines()[0][:120]}")
                    rie.update(d)
                    rie["esito"] = f"Errore: {d['diagnosi']}"
                    self.log(f"{nome}: errore ({type(e).__name__})")
                    page = self._pagina_nuova(ctx, page)
                riepilogo.append(rie)
            self.avanzamento(len(da_fare), len(da_fare), "Fatto")
            browser.close()
        return risultati, riepilogo

    def _diagnosi(self, page, comune, cartella, motivo):
        """Salva screenshot e testo della pagina dell'albo che non si è riusciti a leggere, e prova a dire perché."""
        info = {"url": "", "titolo": "", "testo": "", "frame": [], "tabelle": 0, "link": 0}
        try:
            info = page.evaluate("""() => ({
                url: location.href, titolo: document.title,
                testo: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 3000),
                tabelle: document.querySelectorAll('table').length,
                link: document.querySelectorAll('a[href]').length,
                frame: Array.from(document.querySelectorAll('iframe')).map(f => f.src).slice(0, 5)
            })""")
        except Exception:
            pass
        t = L.norm(info.get("titolo", "") + " " + info.get("testo", ""))
        stato = getattr(self, "ultimo_stato", None)
        if stato in (403, 429) or re.search(r"captcha|access denied|accesso negato|forbidden|request unsuccessful|incapsula"
                                             r"|are you a robot|sei un robot|bot detection|cloudflare|attention required"
                                             r"|richiesta bloccata|too many requests", t):
            diagnosi = "bloccato dal sito (protezione anti-robot)"
        elif "timeout" in motivo.lower():
            diagnosi = "il sito non risponde (timeout)"
        elif stato and stato >= 400:
            diagnosi = f"il sito risponde con errore {stato}"
        elif len(info.get("testo", "")) < 80:
            diagnosi = "pagina vuota o non caricata"
        elif info.get("frame"):
            diagnosi = "l'elenco è dentro un riquadro (iframe) non letto"
        elif re.search(r"nessun (atto|documento|risultato)|non ci sono|nessuna pubblicazione|0 risultati", t):
            diagnosi = "l'albo risulta vuoto in questo momento"
        elif info.get("tabelle", 0) == 0:
            diagnosi = "pagina senza elenco: forse è un menu, serve il link alla lista degli atti"
        else:
            diagnosi = "elenco presente ma in un formato non riconosciuto"
        out = {"diagnosi": diagnosi, "diag_stato": stato, "diag_url": info.get("url", "")}
        if cartella:
            try:
                os.makedirs(cartella, exist_ok=True)
                base = f"{comune.get('sigla', '')}-{L.slug(comune['nome'])}".strip("-")
                try:
                    page.screenshot(path=os.path.join(cartella, base + ".jpg"), type="jpeg", quality=45, timeout=10000)
                    out["screenshot"] = base + ".jpg"
                except Exception:
                    pass
                with open(os.path.join(cartella, base + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"comune": comune["nome"], "sigla": comune.get("sigla", ""), "albo": comune.get("albo"),
                               "motivo": motivo, "diagnosi": diagnosi, "stato_http": stato, **info}, f, ensure_ascii=False, indent=1)
            except Exception:
                pass
        self.log(f"  diagnosi: {diagnosi}")
        return out

    def _righe_pagina(self, page):
        righe = []
        for fi, frame in enumerate(page.frames):
            try:
                for r in frame.evaluate(JS_RIGHE):
                    r["frame"] = fi
                    righe.append(r)
            except Exception:
                pass
        return righe

    def _successiva(self, page):
        for frame in page.frames:
            try:
                i = frame.evaluate(JS_SUCCESSIVA, "a, button, [role=button]")
            except Exception:
                continue
            if i >= 0:
                try:
                    frame.locator("a, button, [role=button]").nth(i).click(timeout=5000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except PWTimeout:
                        pass
                    page.wait_for_timeout(1200)
                    return True
                except Exception:
                    return False
        return False

    def _vai_alla_lista(self, page, solo_determine, salti=2):
        """Se la pagina è un menu (nessun elenco), segue la voce che porta all'elenco degli atti. Ritorna True se trova righe."""
        for _ in range(salti):
            if self._righe_pagina(page):
                return True
            candidati = []
            for fi, fr in enumerate(page.frames):
                try:
                    els = fr.evaluate(JS_CANDIDATI, "a, button, [role=link], [role=button]")
                except Exception:
                    continue
                for c in els:
                    t = L.norm(c.get("text", "") + " " + c.get("title", ""))
                    h = (c.get("href") or "").lower()
                    if not t or len(t) > 120:
                        continue
                    if re.search(r"matrimon|archivio|storic|ricerca avanzata|scrutator|fornitor|cookie|privacy|accessibil"
                                 r"|amministrazione trasparente|^\d{4}$|accedi|login|spid|stampa|rss|feed", t):
                        continue
                    sc = 0
                    if solo_determine and re.search(r"\bdetermin", t):
                        sc += 12
                    if re.search(r"documenti all.albo|albo pretorio on ?line|albo on ?line|atti in pubblicazione"
                                 r"|pubblicazioni in corso|in pubblicazione|elenco (atti|pubblicazioni|documenti)", t):
                        sc += 9
                    if re.search(r"^albo pretorio$|^albo pretorio informatico$|^albo$", t):
                        sc += 8
                    if re.search(r"vai al servizio|consulta|accedi al servizio", t):
                        sc += 3
                    if re.search(r"papca|dettaglio-albo|mc_p_ricerca|albopretorioconsultazione|ricercaalbo", h):
                        sc += 6
                    if sc >= 6:
                        c.update(score=sc, frame=fi)
                        candidati.append(c)
            if not candidati:
                return False
            candidati.sort(key=lambda c: -c["score"])
            top = candidati[0]
            self.log(f"  la pagina è un menu: seguo «{top['text'][:50]}»")
            if L.href_utile(top["href"]) and not top["href"].startswith("#"):
                self._apri(page, urljoin(page.url, top["href"]))
            else:
                try:
                    page.frames[top["frame"]].locator("a, button, [role=link], [role=button]").nth(top["i"]).click(timeout=5000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except PWTimeout:
                        pass
                    page.wait_for_timeout(1500)
                except Exception:
                    return False
        return bool(self._righe_pagina(page))

    def _leggi_albo(self, page, url, dal, gestisci=None, solo_determine=True):
        """Legge le pagine dell'albo; per ogni atto nuovo chiama gestisci() finché la riga è ancora sulla pagina."""
        self._apri(page, url, cortese=True)
        self.url_lista = None
        if not self._righe_pagina(page):
            if self._vai_alla_lista(page, solo_determine):
                self.url_lista = page.url
        atti, visti, pagine, firma_prec = [], set(), 0, None
        while pagine < MAX_PAGINE:
            self._controlla_stop()
            righe = self._righe_pagina(page)
            firma = "|".join(r["text"][:80] for r in righe[:3])
            if not righe or firma == firma_prec:
                break
            firma_prec = firma
            pagine += 1
            url_pagina = page.url
            nuovi = []
            fresche = None          # righe ritrovate dopo essere tornati all'elenco
            for r in righe:
                if r["text"] in visti:
                    continue
                visti.add(r["text"])
                a = L.riga_in_atto(r)
                atti.append(a)
                nuovi.append(a)
                if gestisci:
                    if fresche is not None:
                        r = {**r, **(fresche.get(r["text"]) or {"id": None})}
                    fr = page.frames[r["frame"]] if r["frame"] < len(page.frames) else page.main_frame
                    self._ricarica_lista = False
                    try:
                        gestisci(page, fr, r, a)
                    except Annullato:
                        raise
                    except Exception as e:
                        self.log(f"  atto non elaborato ({type(e).__name__}: {str(e)[:80]})")
                    if page.url != url_pagina or self._ricarica_lista:
                        # un clic ci ha portato altrove (o ha ricaricato la pagina): torno all'elenco
                        tornato = False
                        if page.url != url_pagina:
                            try:
                                page.go_back(wait_until="domcontentloaded", timeout=20000)
                                self._attendi_pronta(page, secondi=15)
                                tornato = page.url == url_pagina and bool(self._righe_pagina(page))
                            except Exception:
                                pass
                        if not tornato:
                            try:
                                self._apri(page, url_pagina)
                                self._attendi_pronta(page, secondi=15)
                            except Exception:
                                pass
                        fresche = {x["text"]: x for x in self._righe_pagina(page)}
            date = [a["data"] for a in nuovi if a["data"]]
            # albo ordinato dal più recente: se l'intera pagina è più vecchia della data, mi fermo
            if date and max(date) < dal:
                break
            if not self._successiva(page):
                break
        return atti, pagine

    # --------------------------------------------------- allegati di un atto

    def _link_allegato(self, links):
        out = []
        for l in links:
            if L.sembra_allegato(l):
                out.append(l["href"])
            elif l.get("daClic") and re.search(r"download|scarica|allegat|document|\.pdf|\.p7m", (l.get("text", "") + " " + l["href"]).lower()):
                out.append(l["href"])
        return out

    def _attendi_pronta(self, page, secondi=25):
        try:
            page.wait_for_load_state("load", timeout=secondi * 1000)
        except Exception:
            pass
        try:
            page.wait_for_function(JS_PRONTA, timeout=secondi * 1000, polling=500)
        except Exception:
            pass
        page.wait_for_timeout(600)

    def _scarica_cliccando_pagina(self, pg, ctx, comune, atto, cartella, contatore):
        """In una pagina di dettaglio clicca i pulsanti 'Scarica'/'Download' senza link vero e raccoglie i file."""
        scaricati, popup, urls = [], [], []

        def su_dl(d):
            scaricati.append(d)

        def su_pag(p):
            popup.append(p)
            p.on("download", su_dl)

        try:
            n = pg.evaluate(JS_SEGNA_DOWNLOAD_PAGINA)
        except Exception:
            return [], []
        pg.on("download", su_dl)
        ctx.on("page", su_pag)
        try:
            url0 = pg.url
            for i in range(n):
                self._controlla_stop()
                try:
                    pg.locator(f'[data-albi-dlp="{i}"]').first.click(timeout=3000)
                    pg.wait_for_timeout(2500)
                except Exception:
                    continue
                if pg.url != url0:      # il pulsante ha cambiato pagina: mi fermo qui
                    break
            for p in popup:
                try:
                    p.wait_for_load_state("domcontentloaded", timeout=15000)
                    p.wait_for_timeout(1500)
                    if L.sembra_allegato({"href": p.url, "text": ""}):
                        urls.append(p.url)
                except Exception:
                    pass
        finally:
            pg.remove_listener("download", su_dl)
            ctx.remove_listener("page", su_pag)
            for p in popup:
                try:
                    p.close()
                except Exception:
                    pass
        salvati = []
        for d in scaricati:
            salvati += self._salva_download(d, comune, atto, cartella, contatore)
        return salvati, urls

    def _tutti_link(self, page):
        out = []
        for fr in page.frames:
            try:
                out += fr.evaluate(JS_TUTTI_LINK)
            except Exception:
                pass
        return out

    def _chiudi_finestre(self, page):
        try:
            page.keyboard.press("Escape")
            for sel in [".modal.show [data-dismiss]", ".modal.show [data-bs-dismiss]", ".modal.show .close",
                        ".modal.in .close", ".ui-dialog-titlebar-close"]:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=1500)
            page.wait_for_timeout(300)
        except Exception:
            pass

    def _allegati_atto(self, page, frame, riga, atto, link_dett, ctx, comune, cartella, contatore):
        """Trova e scarica gli allegati di un atto: link nella riga, tendine/dettagli aperti, clic sulla riga,
        finestre e schede che si aprono, pagina di dettaglio."""
        urls = self._link_allegato(atto["links"])
        salvati = []
        rid = riga.get("id")
        scaricati, popup = [], []           # download partiti da soli e nuove schede aperte dai clic

        def su_download(d):
            scaricati.append(d)

        def su_pagina(p):
            popup.append(p)
            p.on("download", su_download)

        page.on("download", su_download)
        ctx.on("page", su_pagina)
        html_prima = ""
        diag_nav = None
        try:
            if rid:
                try:
                    html_prima = frame.evaluate(JS_HTML_AMBITO, rid)
                except Exception:
                    pass
                prima = {l["href"] for l in self._tutti_link(page)}
                url_prima = page.url
                info = None
                try:
                    info = frame.evaluate(JS_SEGNA_TENDINE, rid)
                except Exception:
                    pass
                targets = (info or {}).get("targets", [])

                def raccogli():
                    trovati = []
                    try:
                        trovati += self._link_allegato(frame.evaluate(JS_LINK_AMBITO, [rid, targets]))
                    except Exception:
                        pass
                    trovati += self._link_allegato([l for l in self._tutti_link(page) if l["href"] not in prima])
                    return trovati

                def clicca(locator):
                    """Clic e attesa; True se il clic ci ha portato su un'altra pagina."""
                    try:
                        if not locator.count():
                            return False
                        locator.scroll_into_view_if_needed(timeout=2000)
                        locator.click(timeout=3000)
                    except Exception:
                        return False
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except PWTimeout:
                        pass
                    page.wait_for_timeout(1000)
                    if page.url != url_prima:
                        return True
                    try:
                        sparita = frame.locator(f'[data-albi-id="{rid}"]').count() == 0
                    except Exception:
                        sparita = True
                    return sparita          # la riga non c'è più: la pagina è stata ricaricata con il dettaglio

                navigato = False
                # 1) comandi riconosciuti come tendine/dettaglio
                for i in range((info or {}).get("n", 0)):
                    if clicca(frame.locator(f'[data-albi-toggle="{i}"]').first):
                        navigato = True
                        break
                # 2) se non è uscito niente: clic sulla prima cella (tipico delle tabelle con "+" disegnato)
                #    e poi sulla riga stessa (molte piattaforme aprono il dettaglio cliccando la riga)
                if not navigato and not raccogli() and not scaricati and not popup:
                    for sel in (f'[data-albi-id="{rid}"] > td:first-child', f'[data-albi-id="{rid}"]'):
                        if clicca(frame.locator(sel).first):
                            navigato = True
                            break
                        if raccogli() or scaricati or popup:
                            break
                if navigato:                      # il clic ha aperto una pagina di dettaglio
                    self._ricarica_lista = True
                    self._attendi_pronta(page)
                    urls += self._link_allegato(self._tutti_link(page))
                    urls += self._espandi_e_raccogli(page)
                    if not urls and not scaricati:
                        s2, u2 = self._scarica_cliccando_pagina(page, ctx, comune, atto, cartella, contatore)
                        salvati += s2
                        urls += u2
                    if not urls and not salvati and not scaricati:
                        diag_nav = self._diag_pagina(page)
                else:
                    urls += raccogli()
                    # allegati che si scaricano solo cliccando (nessun link vero)
                    if not urls and not scaricati:
                        salvati += self._scarica_cliccando(page, frame, rid, comune, atto, cartella, contatore)
                    self._chiudi_finestre(page)

            # schede aperte dai clic: possono essere il dettaglio o direttamente il file
            for p in popup:
                try:
                    p.wait_for_load_state("domcontentloaded", timeout=15000)
                    p.wait_for_timeout(800)
                    if L.sembra_allegato({"href": p.url, "text": ""}):
                        urls.append(p.url)
                    urls += self._link_allegato(self._tutti_link(p))
                except Exception:
                    pass
        finally:
            page.remove_listener("download", su_download)
            ctx.remove_listener("page", su_pagina)
            for p in popup:
                try:
                    p.close()
                except Exception:
                    pass

        for d in scaricati:
            salvati += self._salva_download(d, comune, atto, cartella, contatore)

        diag_dettaglio = None
        if link_dett and not urls and not salvati:
            p2 = ctx.new_page()
            try:
                self._apri(p2, link_dett, attesa=800)
                self._attendi_pronta(p2, secondi=15)
                urls += self._link_allegato(self._tutti_link(p2))
                urls += self._espandi_e_raccogli(p2)
                if not urls:
                    s2, u2 = self._scarica_cliccando_pagina(p2, ctx, comune, atto, cartella, contatore)
                    salvati += s2
                    urls += u2
                if not urls and not salvati:
                    diag_dettaglio = self._diag_pagina(p2)
            except Exception as e:
                self.log(f"  {comune}: dettaglio non apribile ({type(e).__name__})")
            finally:
                p2.close()

        salvati += self._scarica_url(ctx, comune, atto, list(dict.fromkeys(urls)), cartella, contatore)
        salvati = list(dict.fromkeys(salvati))
        if not salvati:
            self.log(f"  {comune}: nessun allegato trovato per «{atto['oggetto'][:60]}»")
            self._diag_atto(page, frame, rid, comune, atto, html_prima, urls, link_dett, diag_dettaglio or diag_nav)
        return salvati

    def _salva_download(self, d, comune, atto, cartella, contatore):
        try:
            percorso = d.path()
            if not percorso:
                return []
            with open(percorso, "rb") as f:
                dati = f.read()
            if len(dati) < 200:
                return []
            nome = d.suggested_filename or ""
            est = L.estensione(nome or d.url, "", f'filename="{nome}"')
            return [self._salva(comune, atto, cartella, contatore, est, dati)]
        except Exception:
            return []

    def _diag_pagina(self, pg):
        """Fotografa una pagina (di dettaglio) per capire dove sono gli allegati."""
        out = {"url": pg.url}
        try:
            out["elementi"] = pg.evaluate(JS_CLICCABILI, None)
            out["testo"] = pg.evaluate("() => (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 2500)")
            out["_png"] = pg.screenshot(type="jpeg", quality=45, full_page=True, timeout=10000)
        except Exception:
            pass
        return out

    def _diag_atto(self, page, frame, rid, comune, atto, html_prima, urls, link_dett, diag_dettaglio):
        """Per i primi atti senza allegati di ogni comune salva cosa ha visto il robot (per correggere il motore)."""
        cartella = getattr(self, "_diag_dir", None)
        if not cartella or getattr(self, "_diag_contati", 0) >= 2:
            return
        self._diag_contati = getattr(self, "_diag_contati", 0) + 1
        base = f"{L.slug(comune)}-atto{self._diag_contati}"
        dati = {"comune": comune, "oggetto": atto["oggetto"], "data": str(atto["data"]), "url_albo": page.url,
                "link_dettaglio": link_dett, "link_trovati_non_scaricabili": urls[:20], "html_riga_prima": html_prima}
        try:
            os.makedirs(cartella, exist_ok=True)
            if rid:
                dati["html_riga_dopo"] = frame.evaluate(JS_HTML_AMBITO, rid)
                dati["elementi_riga"] = frame.evaluate(JS_CLICCABILI, rid)
                el = frame.locator(f'[data-albi-id="{rid}"]').first
                if el.count():
                    el.scroll_into_view_if_needed(timeout=2000)
            page.screenshot(path=os.path.join(cartella, base + ".jpg"), type="jpeg", quality=45, timeout=10000)
            if diag_dettaglio:
                png = diag_dettaglio.pop("_png", None)
                if png:
                    with open(os.path.join(cartella, base + "-dettaglio.jpg"), "wb") as f:
                        f.write(png)
                dati["pagina_dettaglio"] = diag_dettaglio
            with open(os.path.join(cartella, base + ".json"), "w", encoding="utf-8") as f:
                json.dump(dati, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _espandi_e_raccogli(self, page):
        """In una pagina di dettaglio apre tutte le tendine (Dettaglio, Allegati, ecc.) e raccoglie i link."""
        prima = {l["href"] for l in self._tutti_link(page)}
        for fr in page.frames:
            try:
                n = fr.evaluate(r"""() => {
                  let n = 0;
                  document.querySelectorAll('details:not([open])').forEach(d => { d.open = true; });
                  for (const c of document.querySelectorAll('[aria-expanded="false"], [data-toggle="collapse"], [data-bs-toggle="collapse"], .accordion-button.collapsed, button, a[href^="#"], a[href^="javascript"]')) {
                    if (n >= 10) break;
                    const t = ((c.innerText || '') + ' ' + (c.title || '')).toLowerCase();
                    const tend = c.getAttribute('aria-expanded') === 'false' || /collapse/.test(c.getAttribute('data-toggle') || c.getAttribute('data-bs-toggle') || '') || /collapsed/.test(c.className || '');
                    if (tend || /dettagl|allegat|document|file/.test(t)) c.setAttribute('data-albi-esp', String(n++));
                  }
                  return n;
                }""")
            except Exception:
                continue
            for i in range(n):
                try:
                    fr.locator(f'[data-albi-esp="{i}"]').first.click(timeout=2500)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PWTimeout:
            pass
        dopo = self._tutti_link(page)
        return self._link_allegato([l for l in dopo if l["href"] not in prima])

    def _salva(self, comune, atto, cartella, contatore, est, dati):
        # lo stesso file arrivato per due strade diverse (link e clic) si salva una volta sola
        impronta = (comune, atto["data"], atto["oggetto"], hashlib.sha1(dati).hexdigest())
        if not hasattr(self, "_gia_salvati"):
            self._gia_salvati = {}
        if impronta in self._gia_salvati:
            return self._gia_salvati[impronta]
        chiave = (comune, atto["data"])
        contatore[chiave] = contatore.get(chiave, 0) + 1
        dest = os.path.join(cartella, L.slug(comune))
        os.makedirs(dest, exist_ok=True)
        nome = L.nome_file(comune, atto["data"], contatore[chiave], est)
        with open(os.path.join(dest, nome), "wb") as f:
            f.write(dati)
        rel = os.path.join(L.slug(comune), nome)
        self._gia_salvati[impronta] = rel
        return rel

    def _scarica_cliccando(self, page, frame, rid, comune, atto, cartella, contatore):
        salvati = []
        try:
            n = frame.evaluate(JS_SEGNA_DOWNLOAD, rid)
        except Exception:
            return salvati
        for i in range(n):
            self._controlla_stop()
            try:
                el = frame.locator(f'[data-albi-dl="{i}"]').first
                with page.expect_download(timeout=12000) as dl:
                    el.click(timeout=3000)
                salvati += self._salva_download(dl.value, comune, atto, cartella, contatore)
            except Exception:
                continue
        return salvati

    def _scarica_url(self, ctx, comune, atto, urls, cartella, contatore):
        salvati = []
        for u in urls:
            self._controlla_stop()
            fatto = False
            try:
                r = ctx.request.get(u, timeout=60000)
                ctype = r.headers.get("content-type", "")
                if r.ok and "text/html" not in ctype:
                    body = r.body()
                    if len(body) >= 200:
                        est = L.estensione(u, ctype, r.headers.get("content-disposition", ""))
                        salvati.append(self._salva(comune, atto, cartella, contatore, est, body))
                        fatto = True
            except Exception:
                pass
            if not fatto:
                # alcuni siti danno il file solo a un browser vero (sessione, protezioni): lo apro in una scheda
                salvati += self._scarica_con_browser(ctx, u, comune, atto, cartella, contatore)
        return salvati

    def _scarica_con_browser(self, ctx, url, comune, atto, cartella, contatore):
        p = ctx.new_page()
        try:
            with p.expect_download(timeout=20000) as dl:
                try:
                    p.goto(url, timeout=20000)
                except Exception:
                    pass        # "Download is starting": normale quando l'indirizzo è un file
            return self._salva_download(dl.value, comune, atto, cartella, contatore)
        except Exception:
            return []
        finally:
            try:
                p.close()
            except Exception:
                pass
