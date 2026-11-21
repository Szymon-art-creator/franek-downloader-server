FRANEK DOWNLOADER — prototyp serwera dla Render Free

UWAGA: To nie jest uruchomiona usluga. Publiczne serwisy czesto blokuja adresy IP hostingow.
Darmowy plan ma limity, serwer moze zasypiac, a dostepnosc darmowego planu trzeba potwierdzic w panelu.

1. Utworz PUBLICZNE repozytorium GitHub i wgraj cztery pliki: app.py, Dockerfile, requirements.txt, render.yaml.
2. Na render.com zaloz konto i wybierz New -> Blueprint -> repozytorium GitHub.
3. Sprawdz, czy wybrany plan to Free i nie wymaga platnosci; jesli wymaga, przerwij.
4. Po wdrozeniu otworz https://TWOJ-ADRES.onrender.com/health
5. API: POST /download, JSON {"url":"https://...","format":"mp3","quality":192}

ZANIM PODLACZYSZ DO PUBLICZNEJ STRONY: API wymaga dalszego utwardzenia
(ochrona przed naduzyciami, bezpieczna obsluga przekierowan, limit transferu,
limity uzytkownikow, sprawdzenie regulaminow platform). Nie publikuj tego
prototypu jako otwartej uslugi dla wszystkich bez testow i zabezpieczen.

Pobieraj tylko materialy, do ktorych masz prawa. Brak DRM, brak obchodzenia logowania.
