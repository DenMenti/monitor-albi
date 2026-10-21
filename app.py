"""
Monitor Albi Pretori — webapp locale.
Avvio: doppio clic su avvia.bat (oppure MonitorAlbi.exe). Si apre il browser su http://127.0.0.1:8765
"""
import collections
import datetime as dt
import json
import os
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import logica as L

PORTA = 8765
URL_ELENCO_DEFAULT = "https://www.tuttitalia.it/veneto/provincia-di-belluno/67-comuni/"

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    RISORSE = getattr(sys, "_MEIPASS", BASE)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    RISORSE = BASE

DATI = os.path.join(BASE, "docs", "dati")
RISULTATI = os.path.join(BASE, "risultati")
F_CONFIG = os.path.join(DATI, "config.json")
F_COMUNI = os.path.join(DATI, "comuni.json")
F_CONTROLLI = os.path.join(DATI, "ultimi_controlli.json")

CONFIG_DEFAULT = {
    "url_elenco": URL_ELENCO_DEFAULT,
    "parole": [],
    "esclusioni": ["impegno di spesa", "subappalto"],
    "solo_determine": True,
    "scarica": True,
}


# ---------------------------------------------------------- file json

def leggi(percorso, default):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def scrivi(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    tmp = percorso + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    os.replace(tmp, percorso)


def config():
    c = dict(CONFIG_DEFAULT)
    c.update(leggi(F_CONFIG, {}))
    return c


# ------------------------------------------------------------ lavori

class Lavoro:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.reset()

    def reset(self):
        self.attivo = False
        self.tipo = ""
        self.fatti = 0
        self.totale = 0
        self.corrente = ""
        self.log = collections.deque(maxlen=400)
        self.excel = ""
        self.cartella = ""
        self.esito = ""

    def scrivi_log(self, testo):
        ora = dt.datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log.append(f"{ora}  {testo}")

    def avanzamento(self, fatti, totale, corrente):
        with self.lock:
            self.fatti, self.totale, self.corrente = fatti, totale, corrente

    def stato(self):
        with self.lock:
            return {"attivo": self.attivo, "tipo": self.tipo, "fatti": self.fatti, "totale": self.totale,
                    "corrente": self.corrente, "log": list(self.log), "esito": self.esito,
                    "excel": os.path.relpath(self.excel, RISULTATI) if self.excel else "",
                    "cartella": self.cartella}


LAVORO = Lavoro()


def avvia_lavoro(tipo, funzione):
    with LAVORO.lock:
        if LAVORO.attivo:
            return False
    LAVORO.reset()
    LAVORO.stop.clear()
    LAVORO.attivo = True
    LAVORO.tipo = tipo

    def corsa():
        from motore import Annullato
        try:
            funzione()
        except Annullato:
            LAVORO.esito = "Interrotto. I risultati parziali non sono stati salvati."
            LAVORO.scrivi_log("Interrotto dall'utente")
        except Exception as e:
            LAVORO.esito = f"Errore: {e}"
            LAVORO.scrivi_log("ERRORE: " + traceback.format_exc(limit=3))
        finally:
            LAVORO.attivo = False

    threading.Thread(target=corsa, daemon=True).start()
    return True


def lavoro_trova_albi(visibile):
    from motore import Motore
    cfg = config()
    m = Motore(LAVORO.scrivi_log, LAVORO.avanzamento, LAVORO.stop, visibile)
    comuni = m.trova_albi(cfg["url_elenco"], leggi(F_COMUNI, []))
    scrivi(F_COMUNI, comuni)
    os.makedirs(RISULTATI, exist_ok=True)
    nome = os.path.join(RISULTATI, f"albi_pretori_{dt.date.today().isoformat()}.xlsx")
    L.excel_albi(comuni, nome)
    trovati = sum(1 for c in comuni if c.get("albo"))
    LAVORO.excel = nome
    LAVORO.cartella = RISULTATI
    LAVORO.esito = f"Albo trovato per {trovati} comuni su {len(comuni)}."
    LAVORO.scrivi_log(LAVORO.esito)


def lavoro_monitora(opz):
    from motore import Motore
    comuni = leggi(F_COMUNI, [])
    if opz.get("solo"):
        comuni = [c for c in comuni if c["nome"] in opz["solo"]]
    controlli = leggi(F_CONTROLLI, {})
    oggi = dt.date.today()
    data_fissa = dt.date.fromisoformat(opz["data"]) if opz.get("data") else oggi - dt.timedelta(days=30)

    def dal(nome):
        if opz["modo"] == "ultimo" and controlli.get(nome):
            return dt.date.fromisoformat(controlli[nome])
        return data_fissa

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    cartella = os.path.join(RISULTATI, f"monitoraggio_{stamp}")
    os.makedirs(cartella, exist_ok=True)
    m = Motore(LAVORO.scrivi_log, LAVORO.avanzamento, LAVORO.stop, opz.get("visibile", False))
    risultati, riepilogo = m.monitora(comuni, dal, opz, cartella)

    # aggiorno la data dell'ultimo controllo solo per gli albi letti davvero
    for r in riepilogo:
        if r["esito"].startswith("OK"):
            controlli[r["comune"]] = oggi.isoformat()
    scrivi(F_CONTROLLI, controlli)

    risultati.sort(key=lambda r: (r["comune"], r["data"] or dt.date.min))
    nome = os.path.join(cartella, f"determine_{stamp}.xlsx")
    L.excel_monitoraggio(risultati, riepilogo, nome)
    da_controllare = sum(1 for r in riepilogo if not r["esito"].startswith("OK"))
    LAVORO.excel = nome
    LAVORO.cartella = cartella
    LAVORO.esito = (f"{len(risultati)} determine trovate in {len(riepilogo)} comuni."
                    + (f" {da_controllare} albi da controllare a mano (righe gialle nel riepilogo)." if da_controllare else ""))
    LAVORO.scrivi_log(LAVORO.esito)


# ------------------------------------------------------------- server

class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, dati, codice=200):
        corpo = json.dumps(dati, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(RISORSE, "static", "index.html"), "rb") as f:
                corpo = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif u.path == "/api/stato":
            self._json(LAVORO.stato())
        elif u.path == "/api/config":
            self._json({"config": config(), "comuni": leggi(F_COMUNI, []),
                        "controlli": leggi(F_CONTROLLI, {}), "oggi": dt.date.today().isoformat()})
        elif u.path == "/scarica":
            rel = parse_qs(u.query).get("f", [""])[0]
            percorso = os.path.abspath(os.path.join(RISULTATI, rel))
            if not percorso.startswith(os.path.abspath(RISULTATI)) or not os.path.isfile(percorso):
                return self._json({"errore": "file non trovato"}, 404)
            with open(percorso, "rb") as f:
                corpo = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quote(os.path.basename(percorso))}")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        else:
            self._json({"errore": "non trovato"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            d = self._corpo()
        except Exception:
            return self._json({"errore": "richiesta non valida"}, 400)

        if u.path == "/api/config":
            cfg = config()
            for k in ("url_elenco", "parole", "esclusioni", "solo_determine", "scarica"):
                if k in d:
                    cfg[k] = d[k]
            scrivi(F_CONFIG, cfg)
            if "comuni" in d:
                vecchi = {c["nome"]: c for c in leggi(F_COMUNI, [])}
                for c in d["comuni"]:
                    v = vecchi.get(c["nome"])
                    if v is not None and c.get("albo", "") != v.get("albo", ""):
                        v["albo"] = c.get("albo", "").strip()
                        v["manuale"] = bool(v["albo"])
                        v["note"] = "link inserito a mano" if v["albo"] else v.get("note", "")
                scrivi(F_COMUNI, list(vecchi.values()))
            self._json({"ok": True})

        elif u.path == "/api/trova-albi":
            ok = avvia_lavoro("albi", lambda: lavoro_trova_albi(bool(d.get("visibile"))))
            self._json({"ok": ok, "errore": "" if ok else "C'è già un lavoro in corso"})

        elif u.path == "/api/monitora":
            if not leggi(F_COMUNI, []):
                return self._json({"ok": False, "errore": "Prima cerca i link agli albi (passo 1)."})
            cfg = config()
            opz = {"modo": d.get("modo", "ultimo"), "data": d.get("data") or "",
                   "parole": cfg["parole"], "esclusioni": cfg["esclusioni"],
                   "solo_determine": cfg["solo_determine"], "scarica": cfg["scarica"],
                   "visibile": bool(d.get("visibile")), "solo": d.get("solo") or []}
            ok = avvia_lavoro("monitoraggio", lambda: lavoro_monitora(opz))
            self._json({"ok": ok, "errore": "" if ok else "C'è già un lavoro in corso"})

        elif u.path == "/api/stop":
            LAVORO.stop.set()
            self._json({"ok": True})

        elif u.path == "/api/apri-cartella":
            cartella = LAVORO.cartella or RISULTATI
            os.makedirs(cartella, exist_ok=True)
            try:
                if sys.platform == "win32":
                    os.startfile(cartella)
                elif sys.platform == "darwin":
                    os.system(f'open "{cartella}"')
                else:
                    os.system(f'xdg-open "{cartella}"')
            except Exception:
                pass
            self._json({"ok": True})
        else:
            self._json({"errore": "non trovato"}, 404)


def porta_occupata(porta):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) == 0


def main():
    url = f"http://127.0.0.1:{PORTA}"
    if porta_occupata(PORTA):
        print("Monitor Albi è già aperto: apro il browser.")
        webbrowser.open(url)
        return
    os.makedirs(DATI, exist_ok=True)
    os.makedirs(RISULTATI, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore)
    print("Monitor Albi Pretori in funzione su", url)
    print("Lascia aperta questa finestra. Per chiudere il programma chiudila.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
