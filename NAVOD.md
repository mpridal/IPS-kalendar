# Displej s hodinami – návod

Program: `hodiny.py`
Spouští se sám po přihlášení do Windows (Plánovač úloh, úloha **„Displej hodiny“**) a když spadne, do pár minut se zapne znovu.

## Ovládání (ikony na ploše)

| Ikona na ploše | Co dělá |
|---|---|
| **Displej - spustit** | zapne displej a automatické spouštění po přihlášení |
| **Displej - zastavit** | zhasne displej, ukončí program a vypne automatické spouštění (dokud nedáš „spustit“) |
| **Displej - nastaveni** | otevře nastavení v Poznámkovém bloku |

Ikony vedou na skripty ve složce projektu: `Spustit displej.cmd`, `Zastavit displej.cmd`, `Otevrit nastaveni.cmd`.

## Nastavení
Po uložení souboru `nastaveni.json` se změna projeví **do minuty**, restart není potřeba.
Když v souboru uděláš chybu (např. chybí čárka), ukáže se to **červeným pruhem nahoře na displeji** a program zatím použije výchozí hodnoty.

| Nastavení | Co dělá | Hodnoty |
|---|---|---|
| `mesto` | město pro počasí (hledá se nejdřív v Česku, název je vidět vpravo nahoře) | např. `"Brno"` |
| `jas` | jas displeje přes den (nad 50 víc hřeje) | 0 až 100 |
| `otocit_o_180` | obraz vzhůru nohama, když je displej otočený | `true` / `false` |
| `noc_od`, `noc_do` | kdy je noc; stejné časy = noční režim vypnutý | např. `"22:00"`, `"06:30"` |
| `noc_jas` | jas v noci; 0 = displej v noci zhasne | 0 až 100 |
| `vikend_jako_noc` | o víkendu celý den noční jas | `true` / `false` |
| `pruhlednost_panelu` | průhlednost panelu s kalendářem a počasím | 0 (neprůhledný) až 1 (úplně průhledný) |
| `rozmazani_panelu` | rozmazání tapety pod panelem | 0 (ostrá) až 30 |
| `min_kontrast_textu` | jak moc se hlídá čitelnost textu na tapetě | 1 až 7 |
| `skryvat_soukrome_schuzky` | u soukromých schůzek jen „Soukromá schůzka“ (název se ani neukládá) | `true` / `false` |
| `celodenni_udalosti` | ukazovat celodenní události (dovolená, home office…) | `true` / `false` |
| `pocasi_kazdych_minut` | jak často stahovat počasí | 5 až 180 |
| `kalendar_kazdych_minut` | jak často číst kalendář z Outlooku | 1 až 60 |

Desetinná čísla piš s tečkou (`0.55`), časy v uvozovkách (`"22:00"`).

## Co znamenají upozornění na displeji
- **červený pruh nahoře** – chyba v `nastaveni.json`
- **„před 3 h“ u počasí** – počasí se nedaří stáhnout (asi chybí internet), ukazují se poslední známá data
- **„kalendář naposledy v …“** – Outlook se delší dobu nepodařilo přečíst (zkontroluj, že klasický Outlook běží)
- **tmavá obrazovka „Zobrazení se nepodařilo vykreslit“** – chyba v programu; hodiny jdou dál, podrobnosti jsou v logu

## Když něco nejde
Záznam o běhu je v `log.log` (drží se do 512 kB, starší část je v `log.log.1`).
Kalendář se čte z klasického Outlooku, tapeta z aktuálního pozadí Windows.
Složka projektu je na OneDrivu nastavená jako „Vždy ponechat v tomto zařízení“, aby program šel spustit i bez internetu – toto nastavení neměň.
Verze programu se ukládají v gitu (větev `ips-kalendar`), staré ruční zálohy jsou ve složce `_archiv`.
