# Hostowanie OpenFront dla znajomych (Railway)

Ta konfiguracja uruchamia pełny serwer gry w jednym kontenerze. Gracze wchodzą na ten sam adres, tworzą lobby i mogą do niego dołączyć przez zaproszenie. Serwer musi działać cały czas, a Railway musi kierować WebSockety do tej samej instancji.

## Wdrożenie z GitHuba

1. Utwórz własną kopię repozytorium OpenFrontIO na GitHubie (fork albo własne repo). Nie wysyłaj zmian do repozytorium `openfrontio/OpenFrontIO`.
2. Wyślij do swojej kopii zmienione pliki z folderu gry. `node_modules` nie wysyłaj — Git ignoruje ten folder, a Docker pobierze zależności podczas budowania.
3. W Railway utwórz projekt przez **Deploy from GitHub repo** i wybierz swoją kopię. Railway wykryje główny `Dockerfile` i zbuduje grę.
4. W ustawieniach usługi wygeneruj publiczną domenę. Po wygenerowaniu domeny Railway udostępnia ją kontenerowi jako `RAILWAY_PUBLIC_DOMAIN`.
5. Zostaw jedną replikę. Nie włączaj trybu usypiania usługi: stan meczu znajduje się w pamięci serwera, więc wszystkie osoby w danej grze muszą trafiać do tej samej, stale działającej instancji.
6. Otwórz publiczną domenę w przeglądarce i utwórz lobby. Wyślij znajomym link do lobby albo sam adres serwera.

Nie trzeba dodawać kluczy OpenFront ani Cloudflare. Dockerfile ustawia prywatny tryb gry, obsługę lokalnych identyfikatorów graczy, jeden worker i port otrzymany od Railway. WebSockety przechodzą przez Nginx do tego samego serwera, na którym powstają gry.

## Aktualizacje i stan gier

Po wysłaniu nowego commita Railway może wdrożyć aktualizację automatycznie. Restart serwera kończy trwające mecze, bo ich stan nie jest zapisywany na dysku.

> Hosting stale działającego serwera może wymagać płatnego planu Railway. Przed wdrożeniem sprawdź aktualne limity i koszty na swoim koncie.
