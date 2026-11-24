import sys, time
from playwright.sync_api import sync_playwright
U=["https://www.halleyweb.com/c017085/mc/mc_p_ricerca.php","https://www.halleyweb.com/c017133/mc/mc_p_ricerca.php"]
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
with sync_playwright() as p:
    for nome,kw in [("chromium-headless",{}),("chromium-headless-new",{"args":["--headless=new"]}),("chrome-channel",{"channel":"chrome"}),("msedge-channel",{"channel":"msedge"}),("chromium-visibile",{"headless":False})]:
        try:
            b=p.chromium.launch(headless=kw.pop("headless",True),**kw)
        except Exception as e:
            print(nome,"non avviabile",str(e)[:80]); continue
        for ua in (None,UA):
            ctx=b.new_context(user_agent=ua, locale="it-IT") if ua else b.new_context()
            pg=ctx.new_page()
            for u in U:
                try:
                    r=pg.goto(u,timeout=30000); print(nome,"UA" if ua else "noUA",u[-30:],r.status,len(pg.content()))
                except Exception as e: print(nome,"UA" if ua else "noUA",u[-30:],"ERR",str(e).splitlines()[0][:90])
            ctx.close()
        b.close()
