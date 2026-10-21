"""
esegui.py — versione "a riga di comando" usata da GitHub Actions.

    python esegui.py albi
    python esegui.py monitora --modo ultimo --data 2026-10-01 --solo "Agordo, Alleghe"

Legge e scrive i dati in docs/dati/ (così la webapp su GitHub Pages li vede).
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import threading
import zipfile

import logica as L
from motore import Motore

BASE = os.path.dirname(os.path.abspath(__file__))
DATI = os.path.join(BASE, "docs", "dati")
PUB = os.path.join(BASE, "docs", "risultati")
LAVORO = os.path.join(BASE, "_lavoro")
USCITA = os.path.join(BASE, "_uscita")

CONFIG_DEFAULT = {
    "url_elenco": "https://www.tuttitalia.it/veneto/provincia-di-belluno/67-comuni/",
    "parole": [], "esclusioni": ["impegno di spesa", "subappalto"],
    "solo_determine": True, "scarica": True,
}


def leggi(nome, default):
    try:
        with open(os.path.join(DATI, nome), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def scrivi(nome, dati):
    os.makedirs(DATI, exist_ok=True)
    with open(os.path.join(DATI, nome), "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2, default=str)


def log(t):
    print(t, flush=True)


def avanzamento(fatti, totale, corrente):
    if corrente and corrente != "Fatto":
        print(f"[{fatti + 1}/{totale}] {corrente}", flush=True)


def output_github(**kv):
    f = os.environ.get("GITHUB_OUTPUT")
    if f:
        with open(f, "a", encoding="utf-8") as h:
            for k, v in kv.items():
                h.write(f"{k}={v}\n")


def carica_comuni():
    """Legge l'archivio dei comuni; i vecchi record (solo Belluno) ricevono provincia e sigla."""
    comuni = leggi("comuni.json", [])
    for c in comuni:
        if not c.get("sigla"):
            c.update(regione="Veneto", provincia="Belluno", sigla="BL")
    return comuni


def carica_controlli(comuni):
    """Date dell'ultimo controllo, con le vecchie chiavi (solo nome) convertite in 'Nome (SIGLA)'."""
    controlli = leggi("ultimi_controlli.json", {})
    for c in comuni:
        k = L.chiave(c)
        if c["nome"] in controlli and k not in controlli:
            controlli[k] = controlli.pop(c["nome"])
    return controlli


def unisci(archivio, nuovi):
    per_chiave = {L.chiave(c): c for c in archivio}
    for c in nuovi:
        per_chiave[L.chiave(c)] = c
    return sorted(per_chiave.values(), key=lambda c: (c.get("regione", ""), c.get("sigla", ""), L.norm(c["nome"])))


def registra_run(voce):
    storico = leggi("storico.json", [])
    storico.insert(0, voce)
    scrivi("storico.json", storico[:60])


def cmd_albi(args):
    archivio = carica_comuni()
    sel = {"regione": args.regione or "Veneto", "sigla": args.provincia or "",
           "comuni": [x.strip() for x in (args.comuni or "").split(";") if x.strip()],
           "salta_trovati": args.salta_trovati == "si"}
    descr = sel["regione"] + (f" / {sel['sigla']}" if sel["sigla"] else "") + \
        (f" / {len(sel['comuni'])} comuni" if sel["comuni"] else "")
    log(f"Ricerca albi: {descr}")

    def parziale(fatti):
        scrivi("comuni.json", unisci(archivio, fatti))

    m = Motore(log, avanzamento, threading.Event())
    nuovi = m.trova_albi_selezione(sel, archivio, parziale)
    tutti = unisci(archivio, nuovi)
    scrivi("comuni.json", tutti)

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    os.makedirs(PUB, exist_ok=True)
    nome_x = f"albi_pretori_{stamp}.xlsx"
    L.excel_albi(tutti, os.path.join(PUB, nome_x))
    trovati = sum(1 for c in nuovi if c.get("albo"))
    esito = f"{descr}: albo trovato per {trovati} comuni su {len(nuovi)}. In archivio {len(tutti)} comuni."
    registra_run({"quando": stamp, "tipo": "albi", "esito": esito, "excel": f"risultati/{nome_x}", "zip": ""})
    log(esito)


def cmd_monitora(args):
    cfg = {**CONFIG_DEFAULT, **leggi("config.json", {})}
    comuni = carica_comuni()
    if not comuni:
        sys.exit("Nessun comune: esegui prima la ricerca dei link agli albi.")
    solo = {x.strip() for x in (args.solo or "").split(";") if x.strip()}
    if solo:
        comuni = [c for c in comuni if L.chiave(c) in solo or c["nome"] in solo]
        log(f"Controllo solo {len(comuni)} comuni selezionati")
    controlli = carica_controlli(carica_comuni())
    oggi = dt.date.today()
    data_fissa = dt.date.fromisoformat(args.data) if args.data else oggi - dt.timedelta(days=30)

    def dal(c):
        k = L.chiave(c)
        if args.modo == "ultimo" and controlli.get(k):
            return dt.date.fromisoformat(controlli[k])
        return data_fissa

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    cartella = os.path.join(LAVORO, f"monitoraggio_{stamp}")
    shutil.rmtree(cartella, ignore_errors=True)
    os.makedirs(cartella, exist_ok=True)

    opz = {"parole": cfg["parole"], "esclusioni": cfg["esclusioni"],
           "solo_determine": cfg["solo_determine"], "scarica": cfg["scarica"]}
    m = Motore(log, avanzamento, threading.Event())
    risultati, riepilogo = m.monitora(comuni, dal, opz, cartella)

    for r in riepilogo:
        if r["esito"].startswith("OK"):
            controlli[L.chiave({"nome": r["comune"], "sigla": r.get("sigla")})] = oggi.isoformat()
    scrivi("ultimi_controlli.json", controlli)

    risultati.sort(key=lambda r: (r.get("sigla", ""), r["comune"], r["data"] or dt.date.min))
    nome_x = f"determine_{stamp}.xlsx"
    L.excel_monitoraggio(risultati, riepilogo, os.path.join(cartella, nome_x))
    os.makedirs(PUB, exist_ok=True)
    shutil.copy(os.path.join(cartella, nome_x), os.path.join(PUB, nome_x))

    # zip con Excel + allegati: i link nell'Excel funzionano una volta estratto
    n_allegati = sum(len(r["allegati"]) for r in risultati)
    url_zip = ""
    if n_allegati:
        os.makedirs(USCITA, exist_ok=True)
        nome_zip = f"allegati_{stamp}.zip"
        with zipfile.ZipFile(os.path.join(USCITA, nome_zip), "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(cartella):
                for f in files:
                    p = os.path.join(root, f)
                    z.write(p, os.path.join(f"monitoraggio_{stamp}", os.path.relpath(p, cartella)))
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if repo:
            url_zip = f"https://github.com/{repo}/releases/download/run-{stamp}/{nome_zip}"
        output_github(zip=os.path.join(USCITA, nome_zip), tag=f"run-{stamp}")

    da_controllare = sum(1 for r in riepilogo if not r["esito"].startswith("OK"))
    esito = (f"{len(risultati)} determine trovate in {len(riepilogo)} comuni, {n_allegati} allegati."
             + (f" {da_controllare} albi da controllare a mano." if da_controllare else ""))

    scrivi("ultimo_run.json", {
        "quando": stamp, "esito": esito, "excel": f"risultati/{nome_x}", "zip": url_zip,
        "risultati": [{**r, "data": r["data"].isoformat() if r["data"] else ""} for r in risultati],
        "riepilogo": [{**r, "dal": r["dal"].isoformat() if r["dal"] else ""} for r in riepilogo],
    })
    registra_run({"quando": stamp, "tipo": "monitoraggio", "esito": esito,
                  "excel": f"risultati/{nome_x}", "zip": url_zip})
    log(esito)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("albi")
    a.add_argument("--regione", default="")
    a.add_argument("--provincia", default="", help="sigla, es. BL (vuoto = tutta la regione)")
    a.add_argument("--comuni", default="", help="nomi separati da punto e virgola (vuoto = tutti)")
    a.add_argument("--salta-trovati", dest="salta_trovati", default="si")
    m = sub.add_parser("monitora")
    m.add_argument("--modo", default="ultimo", choices=["ultimo", "data"])
    m.add_argument("--data", default="")
    m.add_argument("--solo", default="")
    args = ap.parse_args()
    {"albi": cmd_albi, "monitora": cmd_monitora}[args.cmd](args)


if __name__ == "__main__":
    main()
