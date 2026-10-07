@echo off
REM ---------------------------------------------------------------------------
REM GUNLUK ARSIV KAYDI
REM   1) arsiv kaydet : bultendeki maclarin oranlarini yazar (acilis `ilk` korunur,
REM                     her cagri `son`u gunceller -> kapanis oranı birikir)
REM   2) arsiv sonuc  : biten maclarin skorunu isler (adaylarin tutup tutmadigi
REM                     ancak bu adimla olculebilir)
REM
REM Zamanlanmis gorev olarak kaydetmek (bir kez, yonetici gerekmez):
REM   schtasks /create /tn "BWM-ArsivKaydet" /tr "\"%~f0\"" /sc daily /st 10:00 /ri 480 /du 24:00 /f
REM Kaldirmak:
REM   schtasks /delete /tn "BWM-ArsivKaydet" /f
REM Durumu gormek:
REM   schtasks /query /tn "BWM-ArsivKaydet"
REM Log: data\arsiv\kayit.log
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
if not exist "data\arsiv" mkdir "data\arsiv"
echo ==== %DATE% %TIME% kaydet >> "data\arsiv\kayit.log"
python bwm.py arsiv kaydet >> "data\arsiv\kayit.log" 2>&1
echo ==== %DATE% %TIME% sonuc >> "data\arsiv\kayit.log"
python bwm.py arsiv sonuc >> "data\arsiv\kayit.log" 2>&1
echo ==== %DATE% %TIME% bitti >> "data\arsiv\kayit.log"
