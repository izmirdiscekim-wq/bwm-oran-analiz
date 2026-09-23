"""
BELGE SENKRONU: Oran analiz'da geliştirilen skill/ajan belgelerini, Claude Code'un HER oturumda yüklediği yerlere kopyalar
  ~/.claude/{skills,agents}                     (global: her klasörde görünür)
  Desktop/BWM_Dosyalari/{skills,agents}          (proje kopyası: CLAUDE.md'nin "agents/ ve skills/" dediği yer)
Kod (agents/*.py) ve kural dosyası (taktikler.txt) KOPYALANMAZ: tek kaynak Oran analiz; global bwm.py bu komutları oraya yönlendirir.

  bwm.py senkron          fark listesi (kuru çalışma)
  bwm.py senkron --yaz    farklı olanları kopyalar (hedefte yoksa oluşturur)
Belge eklenince/değişince tek komut: `bwm.py senkron --yaz`. Yeni skill/ajan dosyasını DOSYALAR'a ekle.
"""
import os, sys, shutil, hashlib, argparse

KOK = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))          # Oran analiz/.claude
HEDEFLER = {"global": os.path.join(os.path.expanduser("~"), ".claude"),
            "proje": os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "BWM_Dosyalari")}
DOSYALAR = ["skills/bwm-nesine-bulten/SKILL.md", "skills/bwm-basketbol-bulten/SKILL.md", "skills/bwm-taktik-tara/SKILL.md", "skills/bwm-bulten-arsiv/SKILL.md",
            "skills/bwm-mac-bul/SKILL.md", "skills/bwm-veri-topla/SKILL.md",
            "agents/nesine-bulteni.md", "agents/basketbol-bulteni.md", "agents/taktik-tarayici.md", "agents/bulten-arsivci.md", "agents/mac-bulucu.md", "agents/veri-toplayici.md"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _ozet(yol):
    with open(yol, "rb") as f:
        return hashlib.sha1(f.read().replace(b"\r\n", b"\n")).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(prog="bwm.py senkron")
    p.add_argument("--yaz", action="store_true", help="farklı olanları kopyala (varsayılan: yalnız listele)")
    a = p.parse_args(argv)
    fark = 0
    for ad, kok in HEDEFLER.items():
        if not os.path.isdir(kok):
            print(f"[{ad}] hedef klasör yok: {kok}")
            continue
        durum = {"aynı": 0, "yeni": 0, "farklı": 0}
        for rel in DOSYALAR:
            src, dst = os.path.join(KOK, rel), os.path.join(kok, rel)
            if not os.path.exists(src):
                print(f"[{ad}] KAYNAK YOK: {rel}")
                continue
            d = "yeni" if not os.path.exists(dst) else ("aynı" if _ozet(src) == _ozet(dst) else "farklı")
            durum[d] += 1
            if d != "aynı":
                fark += 1
                print(f"[{ad}] {d}: {rel}" + ("  -> kopyalandı" if a.yaz else ""))
                if a.yaz:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(src, dst)
        print(f"[{ad}] {kok} | " + ", ".join(f"{k} {v}" for k, v in durum.items()))
    if fark and not a.yaz:
        print("kopyalamak için: bwm.py senkron --yaz")


if __name__ == "__main__":
    main()
