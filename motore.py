"""
motore.py — la parte che usa il browser (Microsoft Edge via Playwright).
"""
import datetime as dt
import os
import re
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import logica as L

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0")
SEL_CLICK = "a, button, [role=link], [role=menuitem], [role=button], [onclick]"
COOKIE_TESTI = ["accetta tutti", "accetto tutti", "accetta", "accetto", "acconsento", "ho capito", "ok", "chiudi"]
MAX_PAGINE = 40

JS_RIGHE = r"""() => {
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const links = e => Array.from(e.querySelectorAll('a[href]')).map(a => ({href: a.href, text: txt(a) || a.title || ''}));
  const out = [];
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
      out.push({headers, cells, text: txt(r), links: links(r)});
    }
  }
  if (out.length < 2) {
    const sel = 'article, .card, li, .list-group-item, .item, [class*="atto"], [class*="pubblicazion"], [class*="documento"]';
    for (const c of document.querySelectorAll(sel)) {
      const t = txt(c);
      if (t.length < 20 || t.length > 3000) continue;
      if (!/\d{1,2}[\/.\-]\d{1,2}[\/.\-]\d{2,4}/.test(t)) continue;
      if (c.querySelector(sel)) continue;
      out.push({headers: [], cells: [], text: t, links: links(c)});
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

    def _apri(self, page, url, attesa=1500):
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
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
        dal_per_comune: funzione(nome) -> date
        opz: parole, esclusioni, solo_determine, scarica
        """
        risultati, riepilogo = [], []
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
                try:
                    atti, pagine = self._leggi_albo(page, c["albo"], dal)
                    rie.update(pagine=pagine, letti=len(atti))
                    trovati = 0
                    contatore = {}
                    for a in atti:
                        if a["data"] and a["data"] < dal:
                            continue
                        parole = L.valuta_atto(a, opz["parole"], opz["esclusioni"], opz["solo_determine"])
                        if not parole:
                            continue
                        self._controlla_stop()
                        trovati += 1
                        link = L.link_dettaglio(a)
                        allegati = []
                        if opz["scarica"]:
                            allegati = self._scarica_allegati(ctx, nome, a, link, cartella, contatore)
                        risultati.append({"comune": nome, "sigla": c.get("sigla", ""), "provincia": c.get("provincia", ""),
                                          "data": a["data"], "tipo": a["tipo"], "numero": a["numero"],
                                          "oggetto": a["oggetto"], "parole": parole, "link": link,
                                          "allegati": allegati})
                    rie["trovati"] = trovati
                    if not atti:
                        rie["esito"] = "Nessuna riga letta: albo da controllare a mano"
                    else:
                        rie["esito"] = f"OK – {trovati} trovati"
                    self.log(f"{nome}: {len(atti)} atti letti su {pagine} pagine, {trovati} trovati")
                except Annullato:
                    raise
                except Exception as e:
                    rie["esito"] = f"Errore: {type(e).__name__}: {str(e)[:100]}"
                    self.log(f"{nome}: errore ({type(e).__name__})")
                    page = self._pagina_nuova(ctx, page)
                riepilogo.append(rie)
            self.avanzamento(len(da_fare), len(da_fare), "Fatto")
            browser.close()
        return risultati, riepilogo

    def _righe_pagina(self, page):
        righe = []
        for frame in page.frames:
            try:
                righe += frame.evaluate(JS_RIGHE)
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

    def _leggi_albo(self, page, url, dal):
        self._apri(page, url)
        atti, visti, pagine, firma_prec = [], set(), 0, None
        while pagine < MAX_PAGINE:
            self._controlla_stop()
            righe = self._righe_pagina(page)
            firma = "|".join(r["text"][:80] for r in righe[:3])
            if not righe or firma == firma_prec:
                break
            firma_prec = firma
            pagine += 1
            nuovi = 0
            for r in righe:
                if r["text"] in visti:
                    continue
                visti.add(r["text"])
                atti.append(L.riga_in_atto(r))
                nuovi += 1
            date = [a["data"] for a in atti[-nuovi:] if a["data"]] if nuovi else []
            # albo ordinato dal più recente: se l'intera pagina è più vecchia della data, mi fermo
            if date and max(date) < dal:
                break
            if not self._successiva(page):
                break
        return atti, pagine

    def _scarica_allegati(self, ctx, comune, atto, link_dett, cartella, contatore):
        urls = [l["href"] for l in atto["links"] if L.sembra_allegato(l)]
        if link_dett:
            p2 = ctx.new_page()
            try:
                self._apri(p2, link_dett, attesa=800)
                for frame in p2.frames:
                    try:
                        links = frame.eval_on_selector_all(
                            "a[href]", "els => els.map(a => ({href: a.href, text: (a.innerText || a.title || '').trim()}))")
                    except Exception:
                        continue
                    urls += [l["href"] for l in links if L.sembra_allegato(l)]
            except Exception as e:
                self.log(f"  {comune}: dettaglio non apribile ({type(e).__name__})")
            finally:
                p2.close()
        unici = list(dict.fromkeys(urls))
        salvati = []
        dest = os.path.join(cartella, L.slug(comune))
        for u in unici:
            self._controlla_stop()
            try:
                r = ctx.request.get(u, timeout=60000)
                if not r.ok:
                    continue
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype:
                    continue  # è una pagina, non un file
                body = r.body()
                if len(body) < 200:
                    continue
                chiave = (comune, atto["data"])
                contatore[chiave] = contatore.get(chiave, 0) + 1
                est = L.estensione(u, ctype, r.headers.get("content-disposition", ""))
                os.makedirs(dest, exist_ok=True)
                nome = L.nome_file(comune, atto["data"], contatore[chiave], est)
                with open(os.path.join(dest, nome), "wb") as f:
                    f.write(body)
                salvati.append(os.path.join(L.slug(comune), nome))
            except Exception:
                continue
        if unici and not salvati:
            self.log(f"  {comune}: allegati presenti ma non scaricabili")
        return salvati
