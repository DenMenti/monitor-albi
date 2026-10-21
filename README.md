# Monitor Albi Pretori

Cerca negli albi pretori dei comuni della provincia di Belluno le determine con riferimenti stradali,
le elenca in Excel e scarica gli allegati rinominati `nome-comune_AAAA-MM-GG_01.pdf`.

**Si usa dal browser, da Mac, PC o telefono.** Il lavoro gira sui server di GitHub (GitHub Actions),
la pagina è pubblicata con GitHub Pages. Non serve installare niente.

## Messa in funzione (una volta sola, ~10 minuti)

1. **Crea il repository.** Su GitHub: *New repository*, nome ad esempio `monitor-albi`, **Public**.
   Poi *Add file → Upload files* e trascina tutto il contenuto di questa cartella
   (compresa la cartella `.github`: su Mac i file che iniziano col punto sono nascosti,
   premi `Cmd + Maiusc + .` nel Finder per vederli). *Commit changes*.
2. **Permessi delle Actions.** *Settings → Actions → General → Workflow permissions* →
   **Read and write permissions** → *Save*.
3. **Pubblica la pagina.** *Settings → Pages* → Source: **Deploy from a branch** →
   Branch **main**, cartella **/docs** → *Save*. Dopo un minuto la pagina è su
   `https://TUO-UTENTE.github.io/monitor-albi/`.
4. **Collega la pagina.** Apri la pagina, premi **Collega GitHub** e segui le istruzioni per creare
   il token (una volta per ogni computer o browser che usi).
5. **Primo giro.** Premi **Cerca i link agli albi** (20–30 minuti), controlla i comuni in giallo,
   inserisci le parole chiave e premi **Avvia il controllo**.

## Come funziona

- `docs/` è la pagina web. In `docs/dati/` ci sono parole chiave, link agli albi, date
  dell'ultimo controllo e risultati: la pagina li legge e li modifica tramite GitHub.
- `.github/workflows/monitora.yml` è il lavoro che gira su GitHub. Parte quando premi i pulsanti
  e, in automatico, **ogni lunedì alle 7**. Per cambiare o togliere l'orario modifica la riga `cron`.
- Excel e allegati di ogni controllo finiscono nelle **Releases** del repository come zip;
  estraendo lo zip i link nell'Excel aprono i file.

## Note

- Repository pubblico: gli atti degli albi sono pubblici, il token resta solo nel tuo browser.
  Con un repository privato GitHub Pages richiede l'abbonamento GitHub Pro.
- I server di GitHub sono all'estero: se qualche sito comunale blocca gli accessi dall'estero,
  quell'albo risulterà "da controllare a mano". In quel caso c'è la versione locale.

## Versione locale (alternativa)

Gira sul computer invece che su GitHub e usa gli stessi file.
- **Windows:** doppio clic su `avvia.bat` (serve Python, con "Add python.exe to PATH").
- **Mac:** doppio clic su `avvia.command` (la prima volta: tasto destro → Apri).
Si apre il browser su `http://127.0.0.1:8765`.
