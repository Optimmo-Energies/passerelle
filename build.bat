@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  Construction de l'executable PasserelleOptimmo.exe (PyInstaller, --onefile)
REM  Pre-requis : python + pip install -r requirements.txt
REM  Resultat   : dist\PasserelleOptimmo.exe (a envoyer aux collegues)
REM ============================================================================
REM
REM  SIGNATURE DE CODE (reduit / supprime l'avertissement SmartScreen)
REM  -----------------------------------------------------------------
REM  La signature est PILOTEE par la variable SIGN_METHOD ci-dessous.
REM  Tant que tu n'as pas le certificat, laisse "none" : le build marche
REM  comme avant, aucune signature n'est tentee.
REM
REM  Valeurs possibles :
REM    azure     -> Azure Artifact Signing (DEFAUT, methode de production)
REM                 Necessite : dotnet tool install --global sign --prerelease
REM                             + Azure CLI installee et "az login" fait une fois
REM    none      -> pas de signature (build de test local uniquement)
REM    signtool  -> certificat sur TOKEN USB  OU  cloud Certum SimplySign
REM                 Desktop (les deux exposent le cert a signtool.exe)
REM    esigner   -> SSL.com eSigner en ligne (CodeSignTool, sans token)
REM
REM  Tu peux aussi la definir sans editer ce fichier :
REM      set SIGN_METHOD=signtool && build.bat
REM ============================================================================

if "%SIGN_METHOD%"=="" set SIGN_METHOD=azure

REM --- Horodatage (obligatoire : garde la signature valide apres expiration) ---
set TIMESTAMP_URL=http://timestamp.sectigo.com

REM --- azure : coordonnees du compte Azure Artifact Signing (Optimmo Energies).
REM     L'endpoint DOIT correspondre a la region du compte (ici North Europe),
REM     sinon la signature est rejetee sans message clair.
set AZ_SIGN_ENDPOINT=https://neu.codesigning.azure.net
set AZ_SIGN_ACCOUNT=optichecksigning
set AZ_SIGN_PROFILE=optimmo-cert

REM --- signtool : laisse vide pour selection auto du cert (/a). Sinon, cible
REM     un cert precis par empreinte SHA1 :  set SIGN_THUMBPRINT=xxxxxxxx...
set SIGN_THUMBPRINT=

REM --- esigner (SSL.com CodeSignTool) : a renseigner le jour venu -------------
set ESIGNER_DIR=C:\CodeSignTool
set ESIGNER_USER=
set ESIGNER_PASS=
set ESIGNER_CRED_ID=
set ESIGNER_TOTP=

echo Construction de PasserelleOptimmo.exe...

REM  --noupx : evite l'empaquetage UPX, qui declenche beaucoup de faux
REM            positifs antivirus. Ne PAS reactiver sans raison.
pyinstaller --onefile --windowed --noconfirm --clean --noupx --name PasserelleOptimmo ^
    --icon icon_app.ico ^
    --hidden-import pystray._win32 ^
    --collect-all windows_toasts ^
    --collect-all winrt ^
    --exclude-module numpy --exclude-module scipy --exclude-module pandas ^
    --exclude-module matplotlib --exclude-module PyQt5 --exclude-module IPython ^
    --add-data "fonts;fonts" ^
    --add-data "icon_tray.png;." ^
    --add-data "icon_tray_alert.png;." ^
    --add-data "icon_header.png;." ^
    --add-data "icon_app.ico;." ^
    main.py

if not exist "dist\PasserelleOptimmo.exe" (
    echo [ERREUR] Build echoue : dist\PasserelleOptimmo.exe introuvable.
    exit /b 1
)

REM ============================================================================
REM  Etape signature
REM ============================================================================
if /I "%SIGN_METHOD%"=="none" (
    echo.
    echo [INFO] SIGN_METHOD=none : exe NON signe. SmartScreen affichera un
    echo        avertissement "editeur inconnu". Ne JAMAIS publier de release
    echo        avec un exe non signe : la reputation SmartScreen repartirait
    echo        de zero. Utilise SIGN_METHOD=azure pour publier.
    goto :done
)

if /I "%SIGN_METHOD%"=="azure"    goto :sign_azure
if /I "%SIGN_METHOD%"=="signtool" goto :sign_signtool
if /I "%SIGN_METHOD%"=="esigner"  goto :sign_esigner
echo [ERREUR] SIGN_METHOD inconnu : "%SIGN_METHOD%"
exit /b 1

:sign_azure
echo.
echo Signature via Azure Artifact Signing (%AZ_SIGN_ACCOUNT% / %AZ_SIGN_PROFILE%)...
where sign >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] Outil "sign" introuvable.
    echo          Installe-le : dotnet tool install --global sign --prerelease
    exit /b 1
)
where az >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] Azure CLI ^("az"^) introuvable dans le PATH.
    echo          Si tu viens de l'installer, un terminal deja ouvert garde
    echo          l'ancien PATH : ferme completement VS Code et rouvre-le.
    exit /b 1
)
sign code artifact-signing "dist\PasserelleOptimmo.exe" ^
    --artifact-signing-endpoint %AZ_SIGN_ENDPOINT% ^
    --artifact-signing-account %AZ_SIGN_ACCOUNT% ^
    --artifact-signing-certificate-profile %AZ_SIGN_PROFILE% ^
    --azure-credential-type azure-cli ^
    --verbosity warning
if errorlevel 1 (
    echo [ERREUR] Signature Azure echouee.
    echo          Si l'erreur parle d'authentification : lance "az login".
    exit /b 1
)
REM  Verification sans Windows SDK : signtool.exe n'est pas requis ici.
powershell -NoProfile -Command "$s = Get-AuthenticodeSignature 'dist\PasserelleOptimmo.exe'; if ($s.Status -ne 'Valid') { Write-Host ('[ERREUR] Signature invalide : ' + $s.Status); exit 1 }; Write-Host ('[OK] Signe par ' + $s.SignerCertificate.Subject)"
if errorlevel 1 exit /b 1
goto :done

:sign_signtool
echo.
echo Signature via signtool (token USB ou Certum SimplySign Desktop)...
where signtool >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] signtool.exe introuvable. Installe le Windows SDK, ou
    echo          lance ce build depuis un "Developer Command Prompt".
    exit /b 1
)
if "%SIGN_THUMBPRINT%"=="" (
    signtool sign /fd SHA256 /tr %TIMESTAMP_URL% /td SHA256 /a ^
        "dist\PasserelleOptimmo.exe"
) else (
    signtool sign /fd SHA256 /tr %TIMESTAMP_URL% /td SHA256 /sha1 %SIGN_THUMBPRINT% ^
        "dist\PasserelleOptimmo.exe"
)
if errorlevel 1 (
    echo [ERREUR] Signature echouee.
    exit /b 1
)
signtool verify /pa /v "dist\PasserelleOptimmo.exe"
if errorlevel 1 (
    echo [ERREUR] Verification de la signature echouee.
    exit /b 1
)
echo [OK] Executable signe et verifie.
goto :done

:sign_esigner
echo.
echo Signature via SSL.com eSigner (CodeSignTool, en ligne)...
if not exist "%ESIGNER_DIR%\CodeSignTool.bat" (
    echo [ERREUR] CodeSignTool introuvable dans %ESIGNER_DIR%.
    echo          Telecharge-le sur https://www.ssl.com/download/codesigntool/
    exit /b 1
)
pushd "%ESIGNER_DIR%"
call CodeSignTool.bat sign ^
    -username="%ESIGNER_USER%" -password="%ESIGNER_PASS%" ^
    -credential_id="%ESIGNER_CRED_ID%" -totp_secret="%ESIGNER_TOTP%" ^
    -input_file_path="%~dp0dist\PasserelleOptimmo.exe" -override
set _rc=%errorlevel%
popd
if not "%_rc%"=="0" (
    echo [ERREUR] Signature eSigner echouee (code %_rc%).
    exit /b 1
)
echo [OK] Executable signe via eSigner.
goto :done

:done
echo.
echo Executable disponible dans dist\PasserelleOptimmo.exe
pause
endlocal
