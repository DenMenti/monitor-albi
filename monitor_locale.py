"""
Monitor Albi Pretori — versione per il computer (Windows, ma funziona anche su Mac).

Stessa pagina della versione online, ma il robot gira su questo computer, con la vostra connessione:
serve per i siti dei comuni che non rispondono ai server di GitHub o li bloccano.

Avvio: doppio clic su MonitorAlbi.exe (oppure "python locale.py"). Si apre il browser su http://127.0.0.1:8766
L'archivio (comuni, parole chiave, risultati, allegati) sta nella cartella "archivio" accanto al programma.
"""
import collections
import datetime as dt
import json
import mimetypes
import os
import shutil
import socket
import sys
import threading
import traceback
import types
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

PORTA = 8766
REPO_ONLINE = os.environ.get("MONITOR_REPO", "DenMenti/monitor-albi")
FILE_DATI = ["config.json", "comuni.json", "ultimi_controlli.json", "segnati.json", "storico.json", "ultimo_run.json"]

# --- cartelle: risorse del programma e archivio dati (accanto all'eseguibile)
if getattr(sys, "frozen", False):
    PROGRAMMA = os.path.dirname(sys.executable)
    RISORSE = os.path.join(getattr(sys, "_MEIPASS", PROGRAMMA), "web")
else:
    PROGRAMMA = os.path.dirname(os.path.abspath(__file__))
    RISORSE = os.path.join(PROGRAMMA, "docs")
ARCHIVIO = os.environ.get("MONITOR_BASE") or os.path.join(PROGRAMMA, "archivio")
os.environ["MONITOR_BASE"] = ARCHIVIO
os.environ["MONITOR_LOCALE"] = "1"
DOCS = os.path.join(ARCHIVIO, "docs")
DATI = os.path.join(DOCS, "dati")


def risorsa(*parti):
    """File del programma: prima in RISORSE/..., poi in RISORSE/dati/... (sorgenti) ."""
    for base in (RISORSE, os.path.join(RISORSE, "dati")):
        p = os.path.join(base, *parti)
        if os.path.exists(p):
            return p
    return os.path.join(RISORSE, *parti)


def prepara_archivio():
    os.makedirs(DATI, exist_ok=True)
    # elenco regioni/province/comuni e impostazioni di partenza
    for nome in ("italia.json", "config.json"):
        dest = os.path.join(DATI, nome)
        if not os.path.exists(dest) and os.path.exists(risorsa(nome)):
            shutil.copy(risorsa(nome), dest)
    # al primo avvio prendo l'archivio dalla versione online (comuni, link, parole chiave, segnati)
    if not os.path.exists(os.path.join(DATI, "comuni.json")):
        try:
            n = importa_da_github()
            print(f"Archivio importato dalla versione online ({n} file).")
        except Exception as e:
            print("Non sono riuscito a importare l'archivio online:", e)


def importa_da_github():
    n = 0
    for nome in FILE_DATI:
        url = f"https://raw.githubusercontent.com/{REPO_ONLINE}/main/docs/dati/{nome}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                dati = r.read()
            json.loads(dati.decode("utf-8"))          # controllo che sia JSON valido
        except Exception:
            continue
        if nome in ("ultimo_run.json", "storico.json") and os.path.exists(os.path.join(DATI, nome)):
            continue                                   # i risultati locali non vanno sovrascritti
        with open(os.path.join(DATI, nome), "wb") as f:
            f.write(dati)
        n += 1
    return n


# ----------------------------------------------------------------- lavori

class Lavoro:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.attivo = False
        self.tipo = ""
        self.fatti = 0
        self.totale = 0
        self.corrente = ""
        self.log = collections.deque(maxlen=500)
        self.esito = ""
        self.errore = False
        self.inizio = None
        self.fine = None

    def scrivi_log(self, testo):
        print(testo, flush=True)
        with self.lock:
            self.log.append(f"{dt.datetime.now():%H:%M:%S}  {testo}")

    def avanzamento(self, fatti, totale, corrente):
        with self.lock:
            self.fatti, self.totale, self.corrente = fatti, totale, corrente

    def stato(self):
        with self.lock:
            return {"attivo": self.attivo, "tipo": self.tipo, "fatti": self.fatti, "totale": self.totale,
                    "corrente": self.corrente, "log": list(self.log)[-200:], "esito": self.esito,
                    "errore": self.errore, "inizio": self.inizio, "fine": self.fine}


LAVORO = Lavoro()


def avvia_lavoro(inputs):
    with LAVORO.lock:
        if LAVORO.attivo:
            return False
    import esegui                                       # importato qui: legge MONITOR_BASE già impostata
    LAVORO.reset()
    LAVORO.attivo = True
    LAVORO.tipo = "albi" if inputs.get("azione") == "albi" else "monitora"
    LAVORO.inizio = dt.datetime.now().isoformat(timespec="seconds")
    esegui.STOP.clear()
    esegui.log = LAVORO.scrivi_log
    esegui.avanzamento = LAVORO.avanzamento

    def corsa():
        from motore import Annullato
        try:
            if LAVORO.tipo == "albi":
                args = types.SimpleNamespace(regione=inputs.get("regione") or "Veneto", provincia=inputs.get("provincia") or "",
                                             comuni=inputs.get("solo") or "", salta_trovati=inputs.get("salta_trovati") or "si")
                esegui.cmd_albi(args)
            else:
                args = types.SimpleNamespace(modo=inputs.get("modo") or "ultimo", data=inputs.get("data") or "",
                                             solo=inputs.get("solo") or "")
                esegui.cmd_monitora(args)
            ultimo = (esegui.leggi("storico.json", []) or [{}])[0]
            LAVORO.esito = ultimo.get("esito", "Fatto.")
        except Annullato:
            LAVORO.esito = "Interrotto. I risultati di questo giro non sono stati salvati."
            LAVORO.errore = True
            LAVORO.scrivi_log("Interrotto dall'utente")
        except SystemExit as e:
            LAVORO.esito = str(e) or "Interrotto."
            LAVORO.errore = True
        except Exception as e:
            LAVORO.esito = f"Errore: {e}"
            LAVORO.errore = True
            LAVORO.scrivi_log("ERRORE: " + traceback.format_exc(limit=4))
        finally:
            LAVORO.fine = dt.datetime.now().isoformat(timespec="seconds")
            LAVORO.attivo = False

    threading.Thread(target=corsa, daemon=True).start()
    return True


def apri_cartella(percorso):
    os.makedirs(percorso, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(percorso)
    elif sys.platform == "darwin":
        os.system(f'open "{percorso}"')
    else:
        os.system(f'xdg-open "{percorso}"')


# ------------------------------------------------------------------ server

class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _invia(self, corpo, tipo="application/json; charset=utf-8", codice=200, extra=None):
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dati, codice=200):
        self._invia(json.dumps(dati, ensure_ascii=False, default=str), codice=codice)

    def _file(self, base, rel, scarica=False):
        percorso = os.path.abspath(os.path.join(base, rel))
        if not percorso.startswith(os.path.abspath(base)) or not os.path.isfile(percorso):
            return self._json({"errore": "file non trovato"}, 404)
        with open(percorso, "rb") as f:
            corpo = f.read()
        tipo = mimetypes.guess_type(percorso)[0] or "application/octet-stream"
        extra = {"Content-Disposition": f'attachment; filename="{os.path.basename(percorso)}"'} if scarica else None
        self._invia(corpo, tipo, extra=extra)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p in ("/", "/index.html"):
            with open(risorsa("index.html"), "rb") as f:
                return self._invia(f.read(), "text/html; charset=utf-8")
        if p.startswith("/api/dati/") or p.startswith("/dati/"):
            nome = os.path.basename(p)
            percorso = os.path.join(DATI, nome)
            if not os.path.exists(percorso) and nome == "italia.json":
                percorso = risorsa("italia.json")
            if not os.path.exists(percorso):
                return self._json({} if nome != "comuni.json" and nome != "storico.json" else [], 200)
            with open(percorso, "rb") as f:
                return self._invia(f.read())
        if p == "/api/stato":
            return self._json(LAVORO.stato())
        if p.startswith("/file/"):
            rel = p[len("/file/"):]
            return self._file(DOCS, rel, scarica=rel.endswith(".xlsx"))
        if p.startswith("/uscita/"):
            return self._file(os.path.join(ARCHIVIO, "_uscita"), p[len("/uscita/"):], scarica=True)
        return self._json({"errore": "non trovato"}, 404)

    def do_PUT(self):
        p = unquote(urlparse(self.path).path)
        if p.startswith("/api/dati/"):
            nome = os.path.basename(p)
            if nome not in FILE_DATI:
                return self._json({"errore": "file non modificabile"}, 400)
            corpo = self._corpo()
            tmp = os.path.join(DATI, nome + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(corpo, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(DATI, nome))
            return self._json({"ok": True})
        return self._json({"errore": "non trovato"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            d = self._corpo()
        except Exception:
            return self._json({"errore": "richiesta non valida"}, 400)
        if p == "/api/avvia":
            ok = avvia_lavoro(d.get("inputs") or {})
            return self._json({"ok": ok, "errore": "" if ok else "C'è già un lavoro in corso"})
        if p == "/api/stop":
            import esegui
            esegui.STOP.set()
            return self._json({"ok": True})
        if p == "/api/apri-cartella":
            ultimo = {}
            try:
                with open(os.path.join(DATI, "ultimo_run.json"), encoding="utf-8") as f:
                    ultimo = json.load(f)
            except Exception:
                pass
            cartella = d.get("cartella") or ultimo.get("cartella") or os.path.join(ARCHIVIO, "_lavoro")
            try:
                apri_cartella(cartella)
            except Exception as e:
                return self._json({"ok": False, "errore": str(e)})
            return self._json({"ok": True})
        if p == "/api/importa":
            try:
                n = importa_da_github()
            except Exception as e:
                return self._json({"ok": False, "errore": str(e)})
            return self._json({"ok": True, "file": n})
        return self._json({"errore": "non trovato"}, 404)


def porta_occupata(porta):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) == 0


def main():
    url = f"http://127.0.0.1:{PORTA}"
    if porta_occupata(PORTA):
        print("Monitor Albi è già aperto: apro il browser.")
        webbrowser.open(url)
        return
    prepara_archivio()
    server = ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore)
    print("=" * 60)
    print(" Monitor Albi Pretori - versione per il computer")
    print(" Indirizzo:", url)
    print(" Archivio: ", ARCHIVIO)
    print(" Lascia aperta questa finestra: chiudendola si spegne il programma.")
    print("=" * 60)
    if not os.environ.get("MONITOR_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
