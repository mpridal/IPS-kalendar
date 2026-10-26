# Hodiny pro Turing Smart Screen 3.5" (revize A):
# datum, čas, svátek, nejbližší schůzky z Outlooku, počasí z Open-Meteo a jmeniny ze svatkyapi.cz.
# Nastavení (město, jas, noční režim, průhlednost panelu…) je v souboru nastaveni.json vedle tohoto skriptu;
# změny se projeví do minuty bez restartu.
# Spuštění s parametrem --nahled jen uloží obrázek nahled.png (bez displeje).
# Program se ukončí (a displej zhasne), když do souboru stav.txt někdo zapíše "zastavit" (skript Zastavit displej.cmd).
# Při vypínání/uspávání počítače čistě ukončí spojení s displejem (obraz ponechá, nebo displej zhasne – viz pri_vypnuti_pc).
# Log, mezipaměť a záznam o zamrznutí jsou mimo OneDrive v %LOCALAPPDATA%\IPS-kalendar.

import faulthandler
import json
import os
import shutil
import sys
import threading
import math
import time
import winreg
from datetime import date, datetime, timedelta

ZAKLAD = os.path.dirname(os.path.abspath(__file__))
# Často zapisované soubory mimo OneDrive (synchronizace je zbytečná a zápis do ní může zdržovat)
DATA_SLOZKA = os.path.join(os.environ.get("LOCALAPPDATA", ZAKLAD), "IPS-kalendar")
os.makedirs(DATA_SLOZKA, exist_ok=True)
os.chdir(DATA_SLOZKA)  # knihovna zapisuje log.log do aktuální složky
sys.path.insert(0, ZAKLAD)

# Hlídač zamrznutí: když se program 5 minut nepohne, zapíše stav všech vláken a ukončí se
# (Plánovač úloh ho do 5 minut spustí znovu). Hlavní smyčka hlídače průběžně odkládá.
SOUBOR_ZAMRZNUTI = os.path.join(DATA_SLOZKA, "zamrznuti.txt")
HLIDAC_SEKUND = 300
_zamrznuti = open(SOUBOR_ZAMRZNUTI, "a", encoding="utf-8")


def hlidac():
    faulthandler.dump_traceback_later(HLIDAC_SEKUND, exit=True, file=_zamrznuti)


def hlidac_vypnout():
    faulthandler.cancel_dump_traceback_later()


hlidac()

# Jednorázový přesun starého logu a mezipaměti z OneDrivu (přesun, ne mazání)
for _nazev in ("log.log", "log.log.1", "hodiny_cache.dat"):
    _stary, _novy = os.path.join(ZAKLAD, _nazev), os.path.join(DATA_SLOZKA, _nazev)
    if os.path.isfile(_stary) and not os.path.exists(_novy):
        try:
            shutil.move(_stary, _novy)
        except OSError:
            pass

# pythonw.exe (spuštění bez okna) nemá konzoli
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
for proud in (sys.stdout, sys.stderr):
    try:
        proud.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import logging
from logging.handlers import RotatingFileHandler

import requests
import serial
import win32crypt
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from library.log import logger
from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation

SOUBOR_LOG = os.path.join(DATA_SLOZKA, "log.log")
logger.setLevel(logging.INFO)
# Log knihovny neumí češtinu a nikdy se nepromazává (backupCount=0) -> nahradit vlastním:
# max. 512 kB, jedna starší kopie log.log.1
for h in list(logging.getLogger().handlers):
    if isinstance(h, RotatingFileHandler):
        logging.getLogger().removeHandler(h)
        h.close()
_log_soubor = RotatingFileHandler(SOUBOR_LOG, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
_log_soubor.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%d.%m.%Y %H:%M:%S"))
logging.getLogger().addHandler(_log_soubor)

SIRKA, VYSKA = 480, 320
SOUBOR_NASTAVENI = os.path.join(ZAKLAD, "nastaveni.json")
SOUBOR_STAV = os.path.join(ZAKLAD, "stav.txt")  # "zastavit" = ukončit program
SOUBOR_CACHE = os.path.join(DATA_SLOZKA, "hodiny_cache.dat")  # šifrováno (Windows DPAPI)
STARA_CACHE = os.path.join(ZAKLAD, "hodiny_cache.json")  # dřívější nešifrovaná verze
OPAKOVAT_PO_CHYBE = 5 * 60  # sekund

# Barvy
BILA = (236, 238, 242)
SEDA = (140, 150, 165)
MODRA = (120, 180, 255)
ZLUTA = (255, 200, 90)
CERVENA = (255, 110, 110)
ORANZOVA = (255, 165, 90)
AZUROVA = (100, 200, 255)
ZELENA = (110, 220, 140)
FIALOVA = (200, 160, 255)

PISMO = os.path.join(ZAKLAD, "res", "fonts", "roboto")

DNY = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
DNY_KRATCE = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
MESICE = ["ledna", "února", "března", "dubna", "května", "června",
          "července", "srpna", "září", "října", "listopadu", "prosince"]

POPIS_POCASI = {
    0: "Jasno", 1: "Skoro jasno", 2: "Polojasno", 3: "Zataženo",
    45: "Mlha", 48: "Mrznoucí mlha",
    51: "Slabé mrholení", 53: "Mrholení", 55: "Silné mrholení",
    56: "Mrznoucí mrholení", 57: "Mrznoucí mrholení",
    61: "Slabý déšť", 63: "Déšť", 65: "Silný déšť",
    66: "Mrznoucí déšť", 67: "Mrznoucí déšť",
    71: "Slabé sněžení", 73: "Sněžení", 75: "Silné sněžení", 77: "Sněhová zrna",
    80: "Slabé přeháňky", 81: "Přeháňky", 82: "Silné přeháňky",
    85: "Sněh. přeháňky", 86: "Sněh. přeháňky",
    95: "Bouřka", 96: "Bouřka, kroupy", 99: "Bouřka, kroupy",
}

_fonty = {}


def font(nazev, velikost, nasobek=1):
    klic = (nazev, velikost * nasobek)
    if klic not in _fonty:
        _fonty[klic] = ImageFont.truetype(os.path.join(PISMO, f"Roboto-{nazev}.ttf"), velikost * nasobek)
    return _fonty[klic]


# ---------------------------------------------------------------- nastavení

# název: (výchozí hodnota, minimum, maximum); text a true/false nemají meze, čas je "HH:MM"
VYCHOZI_NASTAVENI = {
    "mesto": ("Olomouc", None, None),
    "jas": (25, 0, 100),
    "otocit_o_180": (False, None, None),
    "noc_od": ("22:00", None, None),
    "noc_do": ("06:30", None, None),
    "noc_jas": (5, 0, 100),
    "vikend_jako_noc": (False, None, None),
    "pruhlednost_panelu": (0.55, 0.0, 1.0),
    "rozmazani_panelu": (8, 0, 30),
    "min_kontrast_textu": (3.5, 1.0, 7.0),
    "skryvat_soukrome_schuzky": (True, None, None),
    "celodenni_udalosti": (True, None, None),
    "pocasi_kazdych_minut": (15, 5, 180),
    "kalendar_kazdych_minut": (2, 1, 60),
    "pri_vypnuti_pc": ("ponechat", None, None),
}
CASOVE_KLICE = {"noc_od", "noc_do"}
VOLBY = {"pri_vypnuti_pc": ("ponechat", "zhasnout")}

# Aktuální nastavení; po změně souboru se načte znovu (viz obnov_nastaveni)
N = {k: v[0] for k, v in VYCHOZI_NASTAVENI.items()}
CHYBY_NASTAVENI = []  # zobrazí se na displeji
_cas_nastaveni = None


def nacti_nastaveni():
    """Vrátí (nastavení, seznam chyb). Neplatné hodnoty nahradí výchozími."""
    nove = {k: v[0] for k, v in VYCHOZI_NASTAVENI.items()}
    try:
        with open(SOUBOR_NASTAVENI, encoding="utf-8-sig") as f:
            ze_souboru = json.load(f)
        if not isinstance(ze_souboru, dict):
            raise ValueError("soubor nemá tvar { ... }")
    except json.JSONDecodeError as e:
        logger.warning(f"Chyba v nastaveni.json: {e}")
        return nove, [f"Chyba v nastaveni.json (řádek {e.lineno}) – používám výchozí nastavení"]
    except Exception as e:
        logger.warning(f"Nelze načíst nastaveni.json: {e}")
        return nove, ["Nastavení nelze načíst – používám výchozí"]
    chyby = []
    for klic, (vychozi, minimum, maximum) in VYCHOZI_NASTAVENI.items():
        if klic not in ze_souboru:
            continue
        hodnota = ze_souboru[klic]
        try:
            if isinstance(vychozi, bool):
                if not isinstance(hodnota, bool):
                    raise ValueError("má být true nebo false")
            elif klic in VOLBY:
                hodnota = str(hodnota).strip().lower()
                if hodnota not in VOLBY[klic]:
                    raise ValueError("povolené hodnoty: " + ", ".join(VOLBY[klic]))
            elif klic in CASOVE_KLICE:
                hodnota = datetime.strptime(str(hodnota).strip(), "%H:%M").strftime("%H:%M")
            elif isinstance(vychozi, str):
                hodnota = str(hodnota).strip()
                if not hodnota:
                    raise ValueError("nesmí být prázdné")
            else:
                hodnota = max(minimum, min(maximum, type(vychozi)(hodnota)))
            nove[klic] = hodnota
        except Exception as e:
            logger.warning(f"Neplatná hodnota '{klic}' v nastaveni.json ({e}), používám {vychozi}")
            chyby.append(f"Neplatná hodnota „{klic}“ v nastaveni.json – používám {vychozi}")
    return nove, chyby


def obnov_nastaveni():
    """Načte nastaveni.json, pokud se od posledně změnil. Vrátí množinu změněných klíčů."""
    global _cas_nastaveni
    try:
        cas = os.path.getmtime(SOUBOR_NASTAVENI)
    except OSError:
        cas = None
    if cas == _cas_nastaveni:
        return set()
    _cas_nastaveni = cas
    nove, chyby = nacti_nastaveni()
    CHYBY_NASTAVENI[:] = chyby
    zmeny = {k for k in nove if nove[k] != N[k]}
    N.update(nove)
    if zmeny:
        logger.info("Nastavení: " + ", ".join(f"{k}={N[k]}" for k in sorted(zmeny)))
    return zmeny


def je_noc(ted):
    if N["vikend_jako_noc"] and ted.weekday() >= 5:
        return True
    od, do = N["noc_od"], N["noc_do"]
    if od == do:
        return False
    cas = ted.strftime("%H:%M")
    return (od <= cas < do) if od < do else (cas >= od or cas < do)


def ma_skoncit():
    try:
        with open(SOUBOR_STAV, encoding="utf-8-sig") as f:
            return f.read().strip().lower() == "zastavit"
    except OSError:
        return False


# ---------------------------------------------------------------- data

class Data:
    """Poslední známá data z internetu; ukládají se na disk, aby přežila restart i výpadek sítě."""

    def __init__(self):
        self.zamek = threading.Lock()
        self.zmena = threading.Event()
        self.obsah = {}
        try:
            with open(SOUBOR_CACHE, "rb") as f:
                self.obsah = json.loads(win32crypt.CryptUnprotectData(f.read(), None, None, None, 0)[1].decode("utf-8"))
        except FileNotFoundError:
            self._prevezmi_starou_cache()
        except Exception as e:
            logger.warning(f"Cache nelze dešifrovat, začínám bez ní: {e}")

    def _prevezmi_starou_cache(self):
        """Načte dřívější nešifrovanou cache, uloží ji šifrovaně a starý soubor přesune do koše."""
        if not os.path.isfile(STARA_CACHE):
            return
        try:
            with open(STARA_CACHE, encoding="utf-8") as f:
                self.obsah = json.load(f)
            self._uloz()
            from win32com.shell import shell, shellcon
            shell.SHFileOperation((0, shellcon.FO_DELETE, STARA_CACHE, None,
                                   shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT, None, None))
            logger.info("Nešifrovaná cache převedena na šifrovanou, starý soubor je v koši")
        except Exception as e:
            logger.warning(f"Převod staré cache selhal: {e}")

    def _uloz(self):
        # Šifruje se klíčem tvého účtu ve Windows: přečíst to jde jen pod tvým přihlášením na tomto počítači
        sifra = win32crypt.CryptProtectData(json.dumps(self.obsah, ensure_ascii=False).encode("utf-8"),
                                            "hodiny", None, None, None, 0)
        with open(SOUBOR_CACHE, "wb") as f:
            f.write(sifra)

    def get(self, klic):
        with self.zamek:
            return self.obsah.get(klic)

    def set(self, klic, hodnota):
        with self.zamek:
            if self.obsah.get(klic) == hodnota:
                return
            self.obsah[klic] = hodnota
            try:
                self._uloz()
            except Exception as e:
                logger.warning(f"Nelze uložit cache: {e}")
        self.zmena.set()


def najdi_souradnice(data, mesto):
    ulozene = data.get("misto")
    if ulozene and ulozene.get("dotaz") == mesto:
        return ulozene
    vysledky = []
    # Nejdřív v Česku (aby např. "Kostelec" nebyl v zahraničí), pak kdekoli
    for zeme in ("CZ", None):
        parametry = {"name": mesto, "count": 1, "language": "cs"}
        if zeme:
            parametry["countryCode"] = zeme
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=parametry, timeout=15)
        r.raise_for_status()
        vysledky = r.json().get("results") or []
        if vysledky:
            break
    if not vysledky:
        raise ValueError(f"Město '{mesto}' nenalezeno")
    v = vysledky[0]
    misto = {"dotaz": mesto, "nazev": v["name"], "lat": v["latitude"], "lon": v["longitude"],
             "casova_zona": v.get("timezone") or "Europe/Prague"}
    data.set("misto", misto)
    logger.info(f"Souřadnice pro {mesto}: {misto['nazev']}, {v.get('country', '')} {misto['lat']}, {misto['lon']}")
    return misto


def stahni_pocasi(data, mesto):
    misto = najdi_souradnice(data, mesto)
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": misto["lat"], "longitude": misto["lon"],
        "current": "temperature_2m,apparent_temperature,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": misto.get("casova_zona", "Europe/Prague"), "forecast_days": 4,
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    data.set("pocasi", {"cas": time.time(), "mesto": misto["nazev"],
                        "current": j["current"], "daily": j["daily"]})
    logger.info("Počasí aktualizováno")


def stahni_svatek(data, den):
    r = requests.get(f"https://svatkyapi.cz/api/day/{den.isoformat()}", timeout=15)
    r.raise_for_status()
    j = r.json()
    data.set("svatek", {"datum": den.isoformat(), "jmeno": j.get("name"),
                        "statni": j.get("holidayName") if j.get("isHoliday") else None})
    logger.info(f"Svátek aktualizován: {j.get('name')}")


def nacti_kalendar(data):
    """Přečte z klasického Outlooku nejbližší schůzky (dnes a zítra). Jen čte, nic nemění.
    U soukromých schůzek se název vůbec neukládá (pokud je zapnuté skryvat_soukrome_schuzky)."""
    import win32com.client
    polozky = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI").GetDefaultFolder(9).Items
    polozky.IncludeRecurrences = True
    polozky.Sort("[Start]")
    ted = datetime.now()
    od, do = ted.date(), ted.date() + timedelta(days=2)
    hranice = datetime.combine(do, datetime.min.time())
    vyber = polozky.Restrict(f"[Start] < '{do.strftime('%m/%d/%Y')}' AND [End] > '{od.strftime('%m/%d/%Y')}'")
    schuzky = []
    for p in vyber:
        try:
            # Outlook vrací místní čas označený jako UTC -> vezmeme jen hodnoty
            zacatek = datetime(p.Start.year, p.Start.month, p.Start.day, p.Start.hour, p.Start.minute)
            konec = datetime(p.End.year, p.End.month, p.End.day, p.End.hour, p.End.minute)
            if zacatek >= hranice:
                break  # seřazeno podle začátku, další už jsou pozdě
            celodenni = bool(p.AllDayEvent)
            if konec <= ted or (celodenni and not N["celodenni_udalosti"]):
                continue
            if p.MeetingStatus in (5, 7) or p.BusyStatus == 0:  # zrušené nebo "volno"
                continue
            soukroma = p.Sensitivity == 2 and N["skryvat_soukrome_schuzky"]  # 2 = soukromé
            schuzky.append({"od": zacatek.isoformat(), "do": konec.isoformat(), "celodenni": celodenni,
                            "soukroma": soukroma, "nazev": "" if soukroma else (p.Subject or "")})
        except Exception:
            continue
        if len(schuzky) >= 15:
            break

    def poradi(s):
        # Celodenní události nahoře u svého dne (vícedenní, které už začaly, patří k dnešku)
        zacatek = datetime.fromisoformat(s["od"])
        den = max(zacatek.date(), ted.date())
        return den, 0 if s["celodenni"] else 1, zacatek

    data.set("kalendar", {"cas": time.time(), "schuzky": sorted(schuzky, key=poradi)})


def stahovani(data, ihned):
    """Běží na pozadí: počasí podle nastavení, svátek jednou denně. Chyby jen zaloguje.
    Událost `ihned` vynutí okamžité stažení počasí (např. po změně města)."""
    dalsi_pocasi = 0
    dalsi_svatek = 0
    while True:
        ted = time.time()
        if ihned.is_set():
            ihned.clear()
            dalsi_pocasi = 0
        if ted >= dalsi_pocasi:
            try:
                stahni_pocasi(data, N["mesto"])
                dalsi_pocasi = ted + N["pocasi_kazdych_minut"] * 60
            except Exception as e:
                logger.warning(f"Počasí se nepodařilo stáhnout: {e}")
                dalsi_pocasi = ted + OPAKOVAT_PO_CHYBE
        dnes = date.today()
        svatek = data.get("svatek")
        if (not svatek or svatek.get("datum") != dnes.isoformat()) and ted >= dalsi_svatek:
            try:
                stahni_svatek(data, dnes)
            except Exception as e:
                logger.warning(f"Svátek se nepodařilo stáhnout: {e}")
                dalsi_svatek = ted + OPAKOVAT_PO_CHYBE
        ihned.wait(20)


def kalendar_na_pozadi(data, ihned):
    import pythoncom
    pythoncom.CoInitialize()
    while True:
        ihned.clear()
        try:
            nacti_kalendar(data)
        except Exception as e:
            logger.warning(f"Kalendář Outlooku se nepodařilo načíst: {e}")
        ihned.wait(N["kalendar_kazdych_minut"] * 60)


# ---------------------------------------------------------------- kreslení

S = 2  # kreslí se ve dvojnásobném rozlišení a pak zmenší -> hladké okraje


def jas_barvy(rgb):
    r, g, b = ((c / 255) ** 2.2 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def tmava_varianta(barva):
    """Tmavá verze barvy pro text na světlém pozadí."""
    if min(barva) > 170:
        return (20, 24, 32)
    return tuple(int(c * 0.45) for c in barva)


def kontrast(a, b):
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class Tapeta:
    """Aktuální tapeta Windows (i automaticky měněná Spotlight), oříznutá na displej."""

    def __init__(self):
        self.klic = None
        self.ostra = None
        self.rozmazana = None

    def _cesta(self):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
            cesta = winreg.QueryValueEx(k, "WallPaper")[0]
            if cesta and os.path.isfile(cesta):
                return cesta
        except Exception:
            pass
        cesta = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Themes\TranscodedWallpaper")
        return cesta if os.path.isfile(cesta) else None

    def obnov(self):
        cesta = self._cesta()
        try:
            klic = (cesta, os.path.getmtime(cesta)) if cesta else None
        except OSError:
            klic = None
        if klic == self.klic and self.ostra is not None:
            return
        try:
            img = ImageOps.fit(Image.open(cesta).convert("RGB"), (SIRKA * S, VYSKA * S), Image.LANCZOS)
            logger.info(f"Načtena tapeta: {cesta}")
        except Exception:
            # Bez tapety: tmavý přechod
            img = Image.linear_gradient("L").resize((SIRKA * S, VYSKA * S)).rotate(90)
            img = ImageOps.colorize(img, (30, 40, 70), (10, 12, 20))
        self.klic = klic
        self.ostra = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), 0.08)
        self.rozmazana = img.filter(ImageFilter.GaussianBlur(N["rozmazani_panelu"] * S)) if N["rozmazani_panelu"] else self.ostra


tapeta = Tapeta()


class Platno:
    def __init__(self):
        self.img = tapeta.ostra.copy()
        self.d = ImageDraw.Draw(self.img)
        self.svetly_panel = False

    def panel(self, x0, y0, x1, y1, r=18, tint=None, alfa=None):
        if alfa is None:
            alfa = int(160 * (1 - N["pruhlednost_panelu"]))
        box = [int(v * S) for v in (x0, y0, x1, y1)]
        if tint is None:
            # Na světlé tapetě světlé sklo, na tmavé tmavé
            jas = ImageStat.Stat(self.img.crop(box).convert("L")).mean[0]
            tint = (240, 243, 248) if jas > 150 else (12, 16, 28)
            self.svetly_panel = tint[0] > 128
        maska = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(maska).rounded_rectangle(box, r * S, fill=255)
        sklo = Image.blend(tapeta.rozmazana, Image.new("RGB", self.img.size, tint), alfa / 255)
        self.img.paste(sklo, (0, 0), maska)
        okraj = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(okraj).rounded_rectangle(box, r * S, outline=(255, 255, 255, 55), width=S)
        self.img = Image.alpha_composite(self.img.convert("RGBA"), okraj).convert("RGB")
        self.d = ImageDraw.Draw(self.img)

    def barva_pro_tapetu(self, bbox, svetla, tmava):
        """Podle tapety pod textem vybere světlou nebo tmavou barvu s lepším kontrastem.
        Počítá s nejsvětlejšími i nejtmavšími místy; když nestačí ani jedna, jemně upraví tapetu pod textem."""
        vyrez = self.img.crop(bbox).convert("L")
        if vyrez.width == 0 or vyrez.height == 0:
            return svetla
        hist = vyrez.histogram()
        celkem = sum(hist)

        def percentil(p):
            soucet = 0
            for hodnota, pocet in enumerate(hist):
                soucet += pocet
                if soucet >= celkem * p:
                    return hodnota
            return 255

        nejtmavsi, nejsvetlejsi = percentil(0.1), percentil(0.9)
        j_svetla, j_tmava = jas_barvy(svetla), jas_barvy(tmava)
        k_svetla = kontrast(j_svetla, (nejsvetlejsi / 255) ** 2.2)
        k_tmava = kontrast(j_tmava, (nejtmavsi / 255) ** 2.2)
        pouzit_svetlou = k_svetla >= k_tmava
        min_kontrast = N["min_kontrast_textu"]
        if max(k_svetla, k_tmava) < min_kontrast:
            if pouzit_svetlou:  # přitmavit nejsvětlejší místa
                cil = 255 * max(0.0, (j_svetla + 0.05) / min_kontrast - 0.05) ** (1 / 2.2)
                sila, barva_zavoje = 1 - cil / max(nejsvetlejsi, 1), (0, 0, 0)
            else:  # zesvětlit nejtmavší místa
                cil = 255 * min(1.0, min_kontrast * (j_tmava + 0.05) - 0.05) ** (1 / 2.2)
                sila, barva_zavoje = (cil - nejtmavsi) / max(255 - nejtmavsi, 1), (255, 255, 255)
            sila = max(0.0, min(0.75, sila))
            okraj = 10 * S
            maska = Image.new("L", self.img.size, 0)
            ImageDraw.Draw(maska).rounded_rectangle(
                (bbox[0] - okraj, bbox[1] - okraj // 2, bbox[2] + okraj, bbox[3] + okraj // 2), okraj, fill=int(255 * sila))
            maska = maska.filter(ImageFilter.GaussianBlur(8 * S))
            self.img.paste(Image.new("RGB", self.img.size, barva_zavoje), (0, 0), maska)
            self.d = ImageDraw.Draw(self.img)
        return svetla if pouzit_svetlou else tmava

    def delka(self, t, pismo):
        return self.d.textlength(t, font=pismo) / S

    def text(self, x, y, t, pismo, barva, anchor="mm", max_sirka=None, na_tapete=True, tmava_barva=None):
        """Vrací šířku textu. Barva se volí podle jasu pozadí (světlá, nebo tmava_barva)."""
        if max_sirka:
            while self.d.textlength(t, font=pismo) > max_sirka * S and pismo.size > 10 * S:
                pismo = font(os.path.basename(pismo.path)[7:-4], pismo.size // S - 1, S)
        pozice = (x * S, y * S)
        if na_tapete:
            barva = self.barva_pro_tapetu(self.d.textbbox(pozice, t, font=pismo, anchor=anchor),
                                          barva, tmava_barva or tmava_varianta(barva))
        self.d.text(pozice, t, font=pismo, fill=barva, anchor=anchor)
        return self.d.textlength(t, font=pismo) / S

    def mesic(self, cx, cy, vel):
        """Srpek měsíce přes masku (dvě kružnice), takže pod ním zůstane tapeta bez skvrn."""
        r = .3 * vel * S
        cx, cy = cx * S, cy * S
        maska = Image.new("L", self.img.size, 0)
        m = ImageDraw.Draw(maska)
        m.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        m.ellipse((cx - r * .35, cy - r * 1.25, cx + r * 1.65, cy + r * .75), fill=0)
        maska = maska.filter(ImageFilter.GaussianBlur(S * .6))
        self.img.paste(Image.new("RGB", self.img.size, (240, 236, 205)), (0, 0), maska)
        self.d = ImageDraw.Draw(self.img)

    def ikona(self, cx, cy, vel, kod, den=True):
        if not den and kod in (0, 1, 2):
            if kod == 0:
                self.mesic(cx, cy, vel)
            else:
                self.mesic(cx + .16 * vel, cy - .16 * vel, vel * .75)
                self.ikona(cx, cy, vel, 3)
                return
            return
        d, s = self.d, vel * S
        cx, cy = cx * S, cy * S
        tloustka = max(2, int(.07 * s))

        def mrak(dy=0, barva=(225, 230, 240)):
            y = cy + dy * s
            obrys = dict(outline=(40, 45, 60), width=max(1, S))
            for e in ((cx - .42 * s, y - .05 * s, cx - .02 * s, y + .33 * s),
                      (cx - .22 * s, y - .30 * s, cx + .22 * s, y + .14 * s),
                      (cx + .02 * s, y - .10 * s, cx + .42 * s, y + .33 * s)):
                d.ellipse(e, fill=barva, **obrys)
            for e in ((cx - .40 * s, y + .02 * s, cx - .04 * s, y + .31 * s),
                      (cx - .20 * s, y - .28 * s, cx + .20 * s, y + .12 * s),
                      (cx + .04 * s, y - .08 * s, cx + .40 * s, y + .31 * s)):
                d.ellipse(e, fill=barva)
            d.rectangle((cx - .22 * s, y + .05 * s, cx + .22 * s, y + .31 * s), fill=barva)

        def slunce(dx=0, dy=0, r=.22):
            x, y = cx + dx * s, cy + dy * s
            for i in range(8):
                a = i * math.pi / 4
                d.line((x + math.cos(a) * r * 1.35 * s, y + math.sin(a) * r * 1.35 * s,
                        x + math.cos(a) * r * 1.85 * s, y + math.sin(a) * r * 1.85 * s), fill=(255, 205, 80), width=tloustka)
            d.ellipse((x - r * s, y - r * s, x + r * s, y + r * s), fill=(255, 205, 80))

        if kod == 0:
            slunce(r=.26)
        elif kod in (1, 2):
            slunce(.16, -.16, .2)
            mrak(.12)
        elif kod in (45, 48):
            for i in range(3):
                d.rounded_rectangle((cx - .4 * s, cy + (-.25 + i * .22) * s, cx + .4 * s, cy + (-.17 + i * .22) * s), S * 2, fill=(205, 210, 220))
        elif kod >= 95:
            mrak(-.1, (170, 178, 195))
            d.polygon([(cx, cy + .15 * s), (cx - .12 * s, cy + .42 * s), (cx + .02 * s, cy + .38 * s), (cx - .06 * s, cy + .6 * s),
                       (cx + .16 * s, cy + .28 * s), (cx + .02 * s, cy + .3 * s)], fill=(255, 210, 70))
        elif kod in (71, 73, 75, 77, 85, 86):
            mrak(-.12)
            for dx, dy in ((-.2, .38), (0, .48), (.2, .38)):
                d.ellipse((cx + (dx - .05) * s, cy + (dy - .05) * s, cx + (dx + .05) * s, cy + (dy + .05) * s), fill=(255, 255, 255))
        elif kod >= 51:
            mrak(-.12, (190, 198, 212))
            for dx in (-.2, 0, .2):
                d.line((cx + dx * s, cy + .3 * s, cx + (dx - .07) * s, cy + .5 * s), fill=(110, 190, 255), width=tloustka)
        else:
            mrak(0, (200, 206, 218))

    def hotovo(self):
        return self.img.resize((SIRKA, VYSKA), Image.LANCZOS)


def zkrat(platno, text, pismo, max_sirka):
    if platno.delka(text, pismo) <= max_sirka:
        return text
    while text and platno.delka(text + "…", pismo) > max_sirka:
        text = text[:-1]
    return text.rstrip() + "…"


def f(nazev, velikost):
    return font(nazev, velikost, S)


def kdy_hodiny(cas_unix, ted):
    kdy = datetime.fromtimestamp(cas_unix)
    return f"{kdy:%H:%M}" if kdy.date() == ted.date() else f"{kdy:%d.%m. %H:%M}"


def vykresli_schuzky(c, data, ted, y_radky):
    kalendar = data.get("kalendar")
    p_cas, p_text = f("Medium", 17), f("Regular", 17)
    if kalendar is None:
        c.text(SIRKA // 2, y_radky[1], "Kalendář: načítám…", f("Regular", 17), SEDA)
        return
    schuzky = [s for s in kalendar["schuzky"] if datetime.fromisoformat(s["do"]) > ted][:len(y_radky)]
    if not schuzky:
        c.text(SIRKA // 2, y_radky[1], "Dnes ani zítra žádné další schůzky", f("Regular", 17), SEDA)
        return
    for y, s in zip(y_radky, schuzky):
        zacatek, konec = datetime.fromisoformat(s["od"]), datetime.fromisoformat(s["do"])
        if s.get("celodenni"):
            kdy, barva = ("celý den", FIALOVA) if zacatek.date() <= ted.date() else ("zítra celý den", (190, 200, 215))
        elif zacatek <= ted:
            kdy, barva = f"teď do {konec:%H:%M}", ZELENA
        elif zacatek.date() == ted.date():
            minut = int((zacatek - ted).total_seconds() // 60) + 1
            kdy = f"{zacatek:%H:%M}" + (f" (za {minut} min)" if minut <= 60 else "")
            barva = ZELENA if minut <= 15 else MODRA
        else:
            kdy, barva = f"zítra {zacatek:%H:%M}", (190, 200, 215)
        x = 22 + c.text(22, y, kdy, p_cas, barva, "lm") + 10
        if s.get("soukroma"):
            c.text(x, y, "Soukromá schůzka", p_text, (175, 185, 200), "lm", tmava_barva=(70, 78, 92))
        else:
            c.text(x, y, zkrat(c, s["nazev"], p_text, 458 - x), p_text, BILA, "lm")


def vykresli(data, ted):
    tapeta.obnov()
    c = Platno()

    # Červený pruh s chybou v nastavení
    posun = 0
    if CHYBY_NASTAVENI:
        zprava = CHYBY_NASTAVENI[0] + (f" (+{len(CHYBY_NASTAVENI) - 1} další)" if len(CHYBY_NASTAVENI) > 1 else "")
        c.panel(8, 4, 472, 30, r=10, tint=(120, 20, 20), alfa=200)
        c.text(240, 17, zprava, f("Medium", 14), (255, 235, 235), max_sirka=450, na_tapete=False)
        posun = 18

    # Nahoře přímo na tapetě: datum, čas, svátek
    datum = f"{DNY[ted.weekday()]} {ted.day}. {MESICE[ted.month - 1]}"
    c.text(20, 20 + posun, datum, f("Medium", 21), BILA, "lm", max_sirka=280)
    c.text(16, 70 + posun, ted.strftime("%H:%M"), f("Bold", 80 - (6 if posun else 0)), BILA, "lm")
    y_svatek = 118 + posun // 2
    svatek = data.get("svatek")
    if svatek and svatek.get("datum") == ted.date().isoformat():
        x = 20
        if svatek.get("jmeno"):
            sloveso = "mají" if " a " in svatek["jmeno"] else "má"
            x += c.text(x, y_svatek, f"Svátek {sloveso} {svatek['jmeno']}", f("Medium", 18), ZLUTA, "lm",
                        tmava_barva=(120, 70, 0))
        if svatek.get("statni"):
            if x > 20:
                x += c.text(x, y_svatek, "  ·  ", f("Medium", 18), BILA, "lm")
            c.text(x, y_svatek, svatek["statni"], f("Medium", 18), CERVENA, "lm", tmava_barva=(150, 20, 20))

    # Vpravo nahoře: město, počasí teď, dnešní max/min
    pocasi = data.get("pocasi")
    if pocasi:
        dy = posun // 2
        if not posun:
            c.text(464, 16, pocasi.get("mesto", ""), f("Medium", 13), (215, 222, 235), "rm", max_sirka=150)
        cu = pocasi["current"]
        c.ikona(322, 56 + dy, 46, cu["weather_code"], cu.get("is_day", 1) == 1)
        c.text(408, 54 + dy, f"{round(cu['temperature_2m'])}°", f("Bold", 38), BILA)
        popis = POPIS_POCASI.get(cu["weather_code"], "")
        stari = time.time() - pocasi["cas"]
        if stari > 60 * 60:  # staré počasí (výpadek internetu)
            pozn = f" · před {int(stari // 3600)} h" if stari < 48 * 3600 else f" · z {kdy_hodiny(pocasi['cas'], ted)}"
            w = c.delka(popis, f("Medium", 16)) + c.delka(pozn, f("Medium", 15))
            x = 380 - w / 2
            x += c.text(x, 93 + dy, popis, f("Medium", 16), BILA, "lm")
            c.text(x, 93 + dy, pozn, f("Medium", 15), CERVENA, "lm", tmava_barva=(150, 20, 20))
        else:
            c.text(380, 93 + dy, popis, f("Medium", 16), BILA, max_sirka=190)
        d = pocasi["daily"]
        if ted.date().isoformat() in d["time"]:
            i = d["time"].index(ted.date().isoformat())
            casti = [("dnes ", f("Regular", 16), BILA, None),
                     (f"{round(d['temperature_2m_max'][i])}°", f("Bold", 17), ORANZOVA, (150, 70, 10)),
                     (f" / {round(d['temperature_2m_min'][i])}°", f("Regular", 16), MODRA, (30, 70, 140))]
            x = 380 - sum(c.delka(t, p) for t, p, _, _ in casti) / 2
            for t, p, b, tb in casti:
                x += c.text(x, 116 + dy, t, p, b, "lm", tmava_barva=tb)

    # Dole skleněný panel: schůzky + předpověď na 3 dny
    c.panel(8, 136, 472, 314)
    vykresli_schuzky(c, data, ted, (156, 180, 204))
    barva_linky = (150, 155, 165) if c.svetly_panel else (255, 255, 255)
    konec_linky = 458
    kalendar = data.get("kalendar")
    if kalendar and time.time() - kalendar["cas"] > max(15, 3 * N["kalendar_kazdych_minut"]) * 60:
        pozn = f"kalendář naposledy v {kdy_hodiny(kalendar['cas'], ted)}"
        konec_linky = 458 - c.delka(pozn, f("Medium", 12)) - 8
        c.text(458, 219, pozn, f("Medium", 12), CERVENA, "rm", tmava_barva=(150, 20, 20))
    c.d.line((22 * S, 219 * S, konec_linky * S, 219 * S), fill=barva_linky, width=1)
    if not pocasi:
        c.text(SIRKA // 2, 265, "Počasí: čekám na internet…", f("Regular", 18), SEDA)
        return c.hotovo()
    d = pocasi["daily"]
    dnes = ted.date().isoformat()
    # Předpověď od zítřka (při starých datech přeskočí dny, které už jsou minulostí)
    dny = [i for i, t in enumerate(d["time"]) if t > dnes][:3]
    for j, i in enumerate(dny):
        cx = 22 + 436 / 6 * (2 * j + 1)
        den = date.fromisoformat(d["time"][i])
        c.ikona(cx - 46, 264, 34, d["weather_code"][i])
        c.text(cx - 20, 240, f"{DNY_KRATCE[den.weekday()]} {den.day}. {den.month}.", f("Medium", 16), (205, 215, 230), "lm")
        x = cx - 20 + c.text(cx - 20, 265, f"{round(d['temperature_2m_max'][i])}°", f("Bold", 22), ORANZOVA, "lm")
        c.text(x + 5, 266, f"{round(d['temperature_2m_min'][i])}°", f("Regular", 19), MODRA, "lm")
        srazky = d["precipitation_probability_max"][i]
        c.text(cx - 20, 292, f"srážky {srazky if srazky is not None else '?'} %", f("Regular", 15), AZUROVA, "lm")
    return c.hotovo()


def nouzova_obrazovka(ted):
    """Jednoduchá obrazovka, když se běžný obraz nepodaří nakreslit (nepotřebuje tapetu ani data)."""
    try:
        img = Image.new("RGB", (SIRKA * S, VYSKA * S), (14, 16, 24))
        d = ImageDraw.Draw(img)
        d.text((240 * S, 34 * S), f"{DNY[ted.weekday()]} {ted.day}. {MESICE[ted.month - 1]}", font=f("Medium", 22), fill=(200, 210, 230), anchor="mm")
        d.text((240 * S, 130 * S), ted.strftime("%H:%M"), font=f("Bold", 120), fill=(240, 240, 245), anchor="mm")
        d.text((240 * S, 238 * S), "Zobrazení se nepodařilo vykreslit.", font=f("Medium", 18), fill=(255, 190, 120), anchor="mm")
        d.text((240 * S, 266 * S), "Zkouším to znovu, podrobnosti jsou v log.log", font=f("Regular", 15), fill=(160, 170, 185), anchor="mm")
        return img.resize((SIRKA, VYSKA), Image.LANCZOS)
    except Exception:
        return Image.new("RGB", (SIRKA, VYSKA), (14, 16, 24))


# ---------------------------------------------------------------- displej

class Displej(LcdCommRevA):
    """Při chybě spojení (odpojení kabelu) nehlásí chybu jen do logu jako knihovna, ale vyhodí výjimku,
    aby se displej znovu celý inicializoval. Jinak by dostal zbytek obrázku v základním stavu a obraz by se rozhodil."""

    def __init__(self, *args, **kwargs):
        self.zhasnuto = False
        super().__init__(*args, **kwargs)

    def WriteLine(self, line: bytes):
        try:
            self.serial_write(line)
        except serial.SerialException as e:  # zahrnuje i SerialTimeoutException
            raise ConnectionError(f"Spojení s displejem přerušeno: {e}") from e

    def openSerial(self):
        # Knihovna při neúspěchu ukončí celý program; tady jen vyhodíme chybu a zkusíme to znovu
        try:
            super().openSerial()
        except SystemExit:
            raise ConnectionError("Port displeje nelze otevřít")

    def je_pripojen(self):
        return self.auto_detect_com_port() is not None


ZAMEK_DISPLEJ = threading.Lock()  # aby se obraz neposílal zároveň s ukončením spojení
_aktualni_lcd = None
_system_konci_od = None  # čas, kdy se počítač začal vypínat/uspávat (None = běží normálně)
SYSTEM_KONCI_MAX = 120  # sekund; déle to trvat nemůže -> počítač se mezitím probudil


def system_konci():
    global _system_konci_od
    if _system_konci_od and time.time() - _system_konci_od > SYSTEM_KONCI_MAX:
        _system_konci_od = None  # pojistka, kdyby nepřišla zpráva o probuzení
    return _system_konci_od is not None


def pri_vypnuti(duvod):
    """Počítač se vypíná nebo uspává: dokončí rozpracovaný obraz a čistě ukončí spojení s displejem."""
    global _system_konci_od
    _system_konci_od = time.time()
    hlidac_vypnout()
    if not ZAMEK_DISPLEJ.acquire(timeout=4):
        return
    try:
        lcd = _aktualni_lcd
        if lcd:
            if N["pri_vypnuti_pc"] == "zhasnout":
                lcd.ScreenOff()
            lcd.closeSerial()
        logger.info(f"Počítač se {duvod}: spojení s displejem ukončeno ({N['pri_vypnuti_pc']})")
    except Exception as e:
        logger.warning(f"Ukončení spojení při {duvod}: {e}")
    finally:
        ZAMEK_DISPLEJ.release()


def hlidej_vypinani():
    """Skryté okno, kterému Windows posílá zprávy o vypínání, odhlášení a uspání."""
    import win32api
    import win32con
    import win32gui
    WM_POWERBROADCAST, PBT_APMSUSPEND, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC = 0x218, 0x4, 0x7, 0x12

    def zpravy(hwnd, msg, wparam, lparam):
        global _system_konci_od
        if msg == win32con.WM_QUERYENDSESSION:
            return True
        if msg == win32con.WM_ENDSESSION:
            if wparam:
                pri_vypnuti("vypíná")
            return 0
        if msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                pri_vypnuti("uspává")
            elif wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC) and _system_konci_od:
                _system_konci_od = None
                logger.info("Počítač se probudil")
            return True
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    try:
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = zpravy
        wc.lpszClassName = "IpsKalendarHodiny"
        wc.hInstance = win32api.GetModuleHandle(None)
        win32gui.RegisterClass(wc)
        # Běžné (neviditelné) okno, ne "message-only" – to by zprávy o vypínání nedostávalo
        win32gui.CreateWindow(wc.lpszClassName, "IPS kalendář", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
        win32gui.PumpMessages()
    except Exception as e:
        logger.warning(f"Hlídání vypínání počítače nefunguje: {e}")


class Probuzeni(Exception):
    """Počítač se probudil -> displej připojit znovu (není to chyba)."""


class Konec(Exception):
    """Požadavek na ukončení programu (stav.txt = zastavit)."""


def pockej_na_displej():
    while system_konci() or Displej.auto_detect_com_port() is None:
        if not system_konci():
            hlidac()
        if ma_skoncit():
            raise Konec()
        time.sleep(2)


def pripoj_displej(ted):
    pockej_na_displej()
    lcd = Displej(com_port="AUTO", display_width=320, display_height=480)
    lcd.Reset()
    lcd.InitializeComm()
    nastav_displej(lcd, ted)
    return lcd


def nastav_displej(lcd, ted):
    """Krátká reinicializace před každým překreslením (zlomek sekundy, nebliká). V noci ztlumí nebo zhasne."""
    noc = je_noc(ted)
    jas = N["noc_jas"] if noc else N["jas"]
    if jas == 0:
        if not lcd.zhasnuto:
            lcd.ScreenOff()
            lcd.zhasnuto = True
            logger.info("Noční režim: displej zhasnut")
        return
    if lcd.zhasnuto:
        lcd.ScreenOn()
        lcd.zhasnuto = False
        logger.info("Displej znovu rozsvícen")
    lcd.SetBrightness(level=jas)
    lcd.SetOrientation(orientation=Orientation.REVERSE_LANDSCAPE if N["otocit_o_180"] else Orientation.LANDSCAPE)


def main():
    obnov_nastaveni()
    data = Data()

    if "--nahled" in sys.argv:
        try:
            stahni_pocasi(data, N["mesto"])
            stahni_svatek(data, date.today())
            nacti_kalendar(data)
        except Exception as e:
            logger.warning(f"Stahování selhalo: {e}")
        vykresli(data, datetime.now()).save(os.path.join(ZAKLAD, "nahled.png"))
        return

    if ma_skoncit():
        logger.info("stav.txt říká 'zastavit' – program nespouštím (spusť přes Spustit displej.cmd)")
        return

    pocasi_ihned = threading.Event()
    kalendar_ihned = threading.Event()
    threading.Thread(target=stahovani, args=(data, pocasi_ihned), daemon=True).start()
    threading.Thread(target=kalendar_na_pozadi, args=(data, kalendar_ihned), daemon=True).start()
    threading.Thread(target=hlidej_vypinani, daemon=True).start()
    posledni_chyba_kresleni = None
    global _aktualni_lcd

    while True:
        lcd = None
        try:
            with ZAMEK_DISPLEJ:
                lcd = pripoj_displej(datetime.now())
                _aktualni_lcd = lcd
            logger.info("Displej připojen")
            while True:
                if system_konci():
                    # Počítač se vypíná/uspává: na displej nic neposílat, po probuzení připojit znovu
                    while system_konci():
                        hlidac_vypnout()
                        time.sleep(1)
                    hlidac()
                    raise Probuzeni()
                zmeny = obnov_nastaveni()
                if "rozmazani_panelu" in zmeny:
                    tapeta.klic = None  # znovu připravit rozmazanou tapetu
                if zmeny & {"mesto", "pocasi_kazdych_minut"}:
                    pocasi_ihned.set()
                if zmeny & {"skryvat_soukrome_schuzky", "celodenni_udalosti", "kalendar_kazdych_minut"}:
                    kalendar_ihned.set()
                ted = datetime.now()
                data.zmena.clear()
                # Chyba při kreslení nesmí vést k restartování displeje -> náhradní obrazovka
                try:
                    obraz = vykresli(data, ted)
                    posledni_chyba_kresleni = None
                except Exception as e:
                    if str(e) != posledni_chyba_kresleni:
                        logger.exception("Chyba při kreslení obrazu, zobrazuji náhradní obrazovku")
                        posledni_chyba_kresleni = str(e)
                    obraz = nouzova_obrazovka(ted)
                with ZAMEK_DISPLEJ:
                    if system_konci():
                        continue
                    nastav_displej(lcd, ted)
                    if not lcd.zhasnuto:
                        lcd.DisplayPILImage(obraz)
                # Čekej na začátek další minuty, nebo na nová data
                dalsi = ted.replace(second=0, microsecond=0) + timedelta(minutes=1)
                while datetime.now() < dalsi and not data.zmena.is_set():
                    data.zmena.wait(min(2.0, max(0.05, (dalsi - datetime.now()).total_seconds())))
                    if not system_konci():
                        hlidac()
                    if ma_skoncit():
                        raise Konec()
                    if system_konci():
                        break
                    if not lcd.je_pripojen():
                        raise ConnectionError("Displej byl odpojen")
        except Konec:
            logger.info("Požadavek na zastavení: zhasínám displej a končím")
            try:
                if lcd:
                    lcd.ScreenOff()
                    lcd.closeSerial()
            except Exception:
                pass
            return
        except Probuzeni:
            _aktualni_lcd = None
            logger.info("Po probuzení počítače připojuji displej znovu")
        except Exception as e:
            _aktualni_lcd = None
            if system_konci():
                pass  # chyba při vypínání/uspávání je očekávaná
            else:
                logger.error(f"Chyba displeje, připojím ho znovu: {e}")
            try:
                if lcd:
                    lcd.closeSerial()
            except Exception:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
