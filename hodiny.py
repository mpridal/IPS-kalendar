# Hodiny pro Turing Smart Screen 3.5" (revize A):
# datum, čas, svátek, 3 nejbližší schůzky z Outlooku, počasí z Open-Meteo a jmeniny ze svatkyapi.cz.
# Nastavení (město, jas, průhlednost panelu…) je v souboru nastaveni.json vedle tohoto skriptu;
# změny se projeví do minuty bez restartu.
# Spuštění s parametrem --nahled jen uloží obrázek nahled.png (bez displeje).

import json
import os
import sys
import threading
import math
import time
import winreg
from datetime import date, datetime, timedelta

ZAKLAD = os.path.dirname(os.path.abspath(__file__))
os.chdir(ZAKLAD)  # knihovna zapisuje log.log do aktuální složky
sys.path.insert(0, ZAKLAD)

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

logger.setLevel(logging.INFO)
# Log knihovny se zapisuje v kódování Windows, které neumí češtinu -> přepnout na UTF-8
for h in logging.getLogger().handlers:
    if isinstance(h, RotatingFileHandler):
        h.acquire()
        try:
            h.stream.close()
            h.encoding = "utf-8"
            h.stream = open(h.baseFilename, "a", encoding="utf-8")
        finally:
            h.release()

SIRKA, VYSKA = 480, 320
SOUBOR_NASTAVENI = os.path.join(ZAKLAD, "nastaveni.json")
SOUBOR_CACHE = os.path.join(ZAKLAD, "hodiny_cache.dat")  # šifrováno (Windows DPAPI)
STARA_CACHE = os.path.join(ZAKLAD, "hodiny_cache.json")  # dřívější nešifrovaná verze
OPAKOVAT_PO_CHYBE = 5 * 60  # sekund

# Barvy
POZADI = (12, 14, 20)
BILA = (236, 238, 242)
SEDA = (140, 150, 165)
LINKA = (45, 52, 66)
MODRA = (120, 180, 255)
ZLUTA = (255, 200, 90)
CERVENA = (255, 110, 110)
ORANZOVA = (255, 165, 90)
AZUROVA = (100, 200, 255)
ZELENA = (110, 220, 140)

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


# název: (výchozí hodnota, minimum, maximum); text a true/false nemají meze
VYCHOZI_NASTAVENI = {
    "mesto": ("Olomouc", None, None),
    "jas": (25, 0, 100),
    "otocit_o_180": (False, None, None),
    "pruhlednost_panelu": (0.55, 0.0, 1.0),
    "rozmazani_panelu": (8, 0, 30),
    "min_kontrast_textu": (3.5, 1.0, 7.0),
    "pocasi_kazdych_minut": (15, 5, 180),
    "kalendar_kazdych_minut": (2, 1, 60),
}

# Aktuální nastavení; po změně souboru se načte znovu (viz obnov_nastaveni)
N = {k: v[0] for k, v in VYCHOZI_NASTAVENI.items()}
_cas_nastaveni = None


def nacti_nastaveni():
    nove = {k: v[0] for k, v in VYCHOZI_NASTAVENI.items()}
    try:
        with open(SOUBOR_NASTAVENI, encoding="utf-8-sig") as f:
            ze_souboru = json.load(f)
    except Exception as e:
        logger.warning(f"Nelze načíst nastaveni.json, používám výchozí: {e}")
        return nove
    for klic, (vychozi, minimum, maximum) in VYCHOZI_NASTAVENI.items():
        if klic not in ze_souboru:
            continue
        hodnota = ze_souboru[klic]
        try:
            if isinstance(vychozi, bool):
                if not isinstance(hodnota, bool):
                    raise ValueError("má být true nebo false")
            elif isinstance(vychozi, str):
                hodnota = str(hodnota).strip()
                if not hodnota:
                    raise ValueError("nesmí být prázdné")
            else:
                hodnota = max(minimum, min(maximum, type(vychozi)(hodnota)))
            nove[klic] = hodnota
        except Exception as e:
            logger.warning(f"Neplatná hodnota '{klic}' v nastaveni.json ({e}), používám {vychozi}")
    return nove


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
    nove = nacti_nastaveni()
    zmeny = {k for k in nove if nove[k] != N[k]}
    N.update(nove)
    if zmeny:
        logger.info("Nastavení: " + ", ".join(f"{k}={N[k]}" for k in sorted(zmeny)))
    return zmeny


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
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": mesto, "count": 1, "language": "cs"}, timeout=15)
    r.raise_for_status()
    vysledky = r.json().get("results") or []
    if not vysledky:
        raise ValueError(f"Město '{mesto}' nenalezeno")
    v = vysledky[0]
    misto = {"dotaz": mesto, "nazev": v["name"], "lat": v["latitude"], "lon": v["longitude"]}
    data.set("misto", misto)
    logger.info(f"Souřadnice pro {mesto}: {misto['lat']}, {misto['lon']}")
    return misto


def stahni_pocasi(data, mesto):
    misto = najdi_souradnice(data, mesto)
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": misto["lat"], "longitude": misto["lon"],
        "current": "temperature_2m,apparent_temperature,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "Europe/Prague", "forecast_days": 4,
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
    """Přečte z klasického Outlooku nejbližší schůzky (dnes a zítra). Jen čte, nic nemění."""
    import win32com.client
    polozky = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI").GetDefaultFolder(9).Items
    polozky.IncludeRecurrences = True
    polozky.Sort("[Start]")
    ted = datetime.now()
    od, do = ted.date(), ted.date() + timedelta(days=2)
    vyber = polozky.Restrict(f"[Start] < '{do.strftime('%m/%d/%Y')}' AND [End] > '{od.strftime('%m/%d/%Y')}'")
    schuzky = []
    for p in vyber:
        try:
            # Outlook vrací místní čas označený jako UTC -> vezmeme jen hodnoty
            zacatek = datetime(p.Start.year, p.Start.month, p.Start.day, p.Start.hour, p.Start.minute)
            konec = datetime(p.End.year, p.End.month, p.End.day, p.End.hour, p.End.minute)
            if p.AllDayEvent or konec <= ted or zacatek >= datetime.combine(do, datetime.min.time()):
                continue
            if p.MeetingStatus in (5, 7) or p.BusyStatus == 0:  # zrušené nebo "volno"
                continue
            schuzky.append({"od": zacatek.isoformat(), "do": konec.isoformat(), "nazev": p.Subject or ""})
        except Exception:
            continue
        if len(schuzky) >= 10:
            break
    data.set("kalendar", {"cas": time.time(), "schuzky": schuzky})


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


def kalendar_na_pozadi(data):
    import pythoncom
    pythoncom.CoInitialize()
    while True:
        try:
            nacti_kalendar(data)
        except Exception as e:
            logger.warning(f"Kalendář Outlooku se nepodařilo načíst: {e}")
        time.sleep(N["kalendar_kazdych_minut"] * 60)


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
        """Vrací šířku textu. Na tapetě se barva volí podle jasu pozadí (světlá, nebo tmava_barva)."""
        if max_sirka:
            while self.d.textlength(t, font=pismo) > max_sirka * S and pismo.size > 10 * S:
                pismo = font(os.path.basename(pismo.path)[7:-4], pismo.size // S - 1, S)
        pozice = (x * S, y * S)
        if na_tapete:
            barva = self.barva_pro_tapetu(self.d.textbbox(pozice, t, font=pismo, anchor=anchor),
                                          barva, tmava_barva or tmava_varianta(barva))
            self.d.text(pozice, t, font=pismo, fill=barva, anchor=anchor)
        else:
            self.d.text(pozice, t, font=pismo, fill=barva, anchor=anchor)
        return self.d.textlength(t, font=pismo) / S

    def ikona(self, cx, cy, vel, kod, den=True):
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
            if not den:
                d.ellipse((x - r * s, y - r * s, x + r * s, y + r * s), fill=(235, 235, 210))
                d.ellipse((x - r * .35 * s, y - r * 1.15 * s, x + r * 1.45 * s, y + r * .65 * s), fill=self.img.getpixel((int(x + r * 1.3 * s), int(y - r * 1.3 * s))))
                return
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


def vykresli_schuzky(c, data, ted, y_radky):
    kalendar = data.get("kalendar")
    p_cas, p_text = f("Medium", 16), f("Regular", 16)
    if kalendar is None:
        c.text(SIRKA // 2, y_radky[1], "Kalendář: načítám…", f("Regular", 16), SEDA)
        return
    schuzky = [s for s in kalendar["schuzky"] if datetime.fromisoformat(s["do"]) > ted][:len(y_radky)]
    if not schuzky:
        c.text(SIRKA // 2, y_radky[1], "Dnes ani zítra žádné další schůzky", f("Regular", 16), SEDA)
        return
    for y, s in zip(y_radky, schuzky):
        zacatek, konec = datetime.fromisoformat(s["od"]), datetime.fromisoformat(s["do"])
        if zacatek <= ted:
            kdy, barva = f"teď do {konec:%H:%M}", ZELENA
        elif zacatek.date() == ted.date():
            minut = int((zacatek - ted).total_seconds() // 60) + 1
            kdy = f"{zacatek:%H:%M}" + (f" (za {minut} min)" if minut <= 60 else "")
            barva = ZELENA if minut <= 15 else MODRA
        else:
            kdy, barva = f"zítra {zacatek:%H:%M}", (190, 200, 215)
        x = 22 + c.text(22, y, kdy, p_cas, barva, "lm") + 10
        c.text(x, y, zkrat(c, s["nazev"], p_text, 458 - x), p_text, BILA, "lm")


def vykresli(data, ted):
    tapeta.obnov()
    c = Platno()

    # Nahoře přímo na tapetě: datum, čas, svátek
    datum = f"{DNY[ted.weekday()]} {ted.day}. {MESICE[ted.month - 1]} {ted.year}"
    c.text(20, 20, datum, f("Medium", 20), BILA, "lm", max_sirka=300, na_tapete=True)
    c.text(16, 70, ted.strftime("%H:%M"), f("Bold", 80), BILA, "lm", na_tapete=True)
    svatek = data.get("svatek")
    if svatek and svatek.get("datum") == ted.date().isoformat():
        x = 20
        if svatek.get("jmeno"):
            sloveso = "mají" if " a " in svatek["jmeno"] else "má"
            x += c.text(x, 118, f"Svátek {sloveso} {svatek['jmeno']}", f("Medium", 17), ZLUTA, "lm",
                        na_tapete=True, tmava_barva=(120, 70, 0))
        if svatek.get("statni"):
            if x > 20:
                x += c.text(x, 118, "  ·  ", f("Medium", 17), BILA, "lm", na_tapete=True)
            c.text(x, 118, svatek["statni"], f("Medium", 17), CERVENA, "lm", na_tapete=True, tmava_barva=(150, 20, 20))

    # Vpravo nahoře: počasí teď
    pocasi = data.get("pocasi")
    if pocasi:
        cu = pocasi["current"]
        c.ikona(330, 58, 50, cu["weather_code"], cu.get("is_day", 1) == 1)
        c.text(412, 56, f"{round(cu['temperature_2m'])}°", f("Bold", 38), BILA, na_tapete=True)
        c.text(372, 104, POPIS_POCASI.get(cu["weather_code"], ""), f("Medium", 16), BILA, na_tapete=True, max_sirka=200)
        stari = time.time() - pocasi["cas"]
        if stari > 60 * 60:
            kdy = datetime.fromtimestamp(pocasi["cas"]).strftime("%H:%M" if stari < 20 * 3600 else "%d.%m. %H:%M")
            c.text(SIRKA - 12, 14, f"offline · data z {kdy}", f("Regular", 12), CERVENA, "rm", na_tapete=True)

    # Dole skleněný panel: 3 schůzky + předpověď na 3 dny
    c.panel(8, 136, 472, 312)
    vykresli_schuzky(c, data, ted, (154, 177, 200))
    c.d.line((22 * S, 216 * S, 458 * S, 216 * S), fill=(150, 155, 165) if c.svetly_panel else (255, 255, 255), width=1)
    if not pocasi:
        c.text(SIRKA // 2, 262, "Počasí: čekám na internet…", f("Regular", 18), SEDA)
        return c.hotovo()
    d = pocasi["daily"]
    dnes = ted.date().isoformat()
    # Předpověď od zítřka (při starých datech přeskočí dny, které už jsou minulostí)
    dny = [i for i, t in enumerate(d["time"]) if t > dnes][:3]
    for j, i in enumerate(dny):
        cx = 22 + 436 / 6 * (2 * j + 1)
        den = date.fromisoformat(d["time"][i])
        c.ikona(cx - 44, 262, 32, d["weather_code"][i])
        c.text(cx - 20, 240, f"{DNY_KRATCE[den.weekday()]} {den.day}. {den.month}.", f("Medium", 15), (200, 210, 225), "lm")
        x = cx - 20 + c.text(cx - 20, 264, f"{round(d['temperature_2m_max'][i])}°", f("Bold", 20), ORANZOVA, "lm")
        c.text(x + 5, 265, f"{round(d['temperature_2m_min'][i])}°", f("Regular", 17), MODRA, "lm")
        srazky = d["precipitation_probability_max"][i]
        c.text(cx - 20, 288, f"srážky {srazky if srazky is not None else '?'} %", f("Regular", 13), AZUROVA, "lm")
    return c.hotovo()


class Displej(LcdCommRevA):
    """Při chybě spojení (odpojení kabelu) nehlásí chybu jen do logu jako knihovna, ale vyhodí výjimku,
    aby se displej znovu celý inicializoval. Jinak by dostal zbytek obrázku v základním stavu a obraz by se rozhodil."""

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


def pockej_na_displej():
    while Displej.auto_detect_com_port() is None:
        time.sleep(2)


def pripoj_displej():
    pockej_na_displej()
    lcd = Displej(com_port="AUTO", display_width=320, display_height=480)
    lcd.Reset()
    lcd.InitializeComm()
    nastav_displej(lcd)
    return lcd


def nastav_displej(lcd):
    """Krátká reinicializace před každým překreslením (zlomek sekundy, nebliká)."""
    lcd.SetBrightness(level=N["jas"])
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

    stahnout_ihned = threading.Event()
    threading.Thread(target=stahovani, args=(data, stahnout_ihned), daemon=True).start()
    threading.Thread(target=kalendar_na_pozadi, args=(data,), daemon=True).start()

    while True:
        lcd = None
        try:
            lcd = pripoj_displej()
            logger.info("Displej připojen")
            while True:
                zmeny = obnov_nastaveni()
                if "rozmazani_panelu" in zmeny:
                    tapeta.klic = None  # znovu připravit rozmazanou tapetu
                if zmeny & {"mesto", "pocasi_kazdych_minut"}:
                    stahnout_ihned.set()
                ted = datetime.now()
                data.zmena.clear()
                obraz = vykresli(data, ted)
                nastav_displej(lcd)
                lcd.DisplayPILImage(obraz)
                # Čekej na začátek další minuty, nebo na nová data
                dalsi = ted.replace(second=0, microsecond=0) + timedelta(minutes=1)
                while datetime.now() < dalsi and not data.zmena.is_set():
                    data.zmena.wait(min(2.0, max(0.05, (dalsi - datetime.now()).total_seconds())))
                    if not lcd.je_pripojen():
                        raise ConnectionError("Displej byl odpojen")
        except Exception as e:
            logger.error(f"Chyba displeje, připojím ho znovu: {e}")
            try:
                if lcd:
                    lcd.closeSerial()
            except Exception:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
