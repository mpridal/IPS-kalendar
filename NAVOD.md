# Displej s hodinami – návod

Program: `hodiny.py`
Spouští se sám po přihlášení do Windows (Plánovač úloh, úloha **„Displej hodiny“**) a když spadne, do pár minut se zapne znovu.

Příkazy níže zadávej do **PowerShellu** (Start → napiš „PowerShell“ → Enter).

## Zastavit
```
Stop-ScheduledTask "Displej hodiny"; Get-CimInstance Win32_Process | ? CommandLine -like "*hodiny.py*" | % { Stop-Process -Id $_.ProcessId -Force }
```
Pozor: Plánovač ho každých 5 minut zase spustí. Aby zůstal vypnutý, nejdřív ho zakaž:
```
Disable-ScheduledTask "Displej hodiny"
```

## Znovu spustit
```
Enable-ScheduledTask "Displej hodiny"; Start-ScheduledTask "Displej hodiny"
```

## Změnit nastavení (město, jas, vzhled…)
Otevři v Poznámkovém bloku soubor `nastaveni.json`, změň hodnotu a ulož. **Změna se projeví do minuty, restart není potřeba.**

| Nastavení | Co dělá | Hodnoty |
|---|---|---|
| `mesto` | město pro počasí (souřadnice se dohledají samy) | např. `"Brno"` |
| `jas` | jas displeje (nad 50 víc hřeje) | 0 až 100 |
| `otocit_o_180` | obraz vzhůru nohama, když je displej otočený | `true` / `false` |
| `pruhlednost_panelu` | průhlednost panelu s kalendářem a počasím | 0 (neprůhledný) až 1 (úplně průhledný) |
| `rozmazani_panelu` | rozmazání tapety pod panelem | 0 (ostrá) až 30 |
| `min_kontrast_textu` | jak moc se hlídá čitelnost textu na tapetě | 1 až 7 |
| `pocasi_kazdych_minut` | jak často stahovat počasí | 5 až 180 |
| `kalendar_kazdych_minut` | jak často číst kalendář z Outlooku | 1 až 60 |

Stejné vysvětlivky jsou i přímo v souboru (část `_vysvetlivky`, tu program ignoruje).
Desetinná čísla piš s tečkou (`0.55`). Když zadáš nesmysl, program použije výchozí hodnotu a zapíše to do logu.

## Když něco nejde
Záznam o běhu je v `log.log`.
Kalendář se čte z klasického Outlooku, tapeta z aktuálního pozadí Windows.
