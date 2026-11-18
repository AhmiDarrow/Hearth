#define MyAppName "Hearth"
#ifndef MyAppVersion
  ; Overridden at build time from hearth.__version__, the single source of truth.
  #define MyAppVersion "0.3.0"
#endif
#ifndef MyAppVersionInfo
  #define MyAppVersionInfo "0.3.0"
#endif
#define MyAppPublisher "Ahmi Darrow"
#define MyAppURL "https://github.com/AhmiDarrow/Hearth"
#define MyAppUpdatesURL "https://github.com/AhmiDarrow/Hearth/releases"
#define MyAppExeName "Hearth.exe"

[Setup]
AppId={{8E4C1F2A-9B3D-4A71-8E6C-2F5D9A1B0C44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppUpdatesURL}
DefaultDirName={localappdata}\Programs\Hearth
DefaultGroupName=Hearth
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
; Allow /CURRENTUSER (and /ALLUSERS) on the command line without showing
; an "install for all users?" wizard page. Without this, /CURRENTUSER
; makes Setup abort immediately with exit code 1.
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousPrivileges=no
UsePreviousAppDir=no
OutputDir=..\dist
OutputBaseFilename=HearthSetup
SetupIconFile=hearth.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=yes
; Do not set AppMutex: that directive shows a "close Hearth" dialog if the
; app is still running. CloseApplications=force plus a taskkill fallback
; kill only Hearth.exe whose path is under this install, and the app also
; exits itself first.
SetupMutex=HearthSetupSingleInstance
MinVersion=10.0
VersionInfoVersion={#MyAppVersionInfo}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Hearth home group installer
VersionInfoProductName={#MyAppName}
LicenseFile=..\LICENSE
#ifdef SIGNBUILD
SignTool=hearthsign $f
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
; Inno Setup 6.7.3 does not ship Chinese Simplified. This is the unofficial
; translation from the matching jrsoftware tag (UTF-8, LanguageCodePage 936).
Name: "chinesesimplified"; MessagesFile: "isl\ChineseSimplified.isl"

[CustomMessages]
DesktopIcon=Create a desktop shortcut
Autostart=Start Hearth when I sign in
TrustCert=Trust the Hearth publisher certificate (optional; helps Windows recognize updates)
AdditionalOptions=Additional options:
OpenNow=Open Hearth now
IconComment=Share folders and chat with the other PCs in the house.
spanish.DesktopIcon=Crear un acceso directo en el escritorio
spanish.Autostart=Iniciar Hearth al iniciar sesión
spanish.TrustCert=Confiar en el certificado del publicador de Hearth (opcional; ayuda a Windows a reconocer las actualizaciones)
spanish.AdditionalOptions=Opciones adicionales:
spanish.OpenNow=Abrir Hearth ahora
spanish.IconComment=Comparte carpetas y chatea con los otros PC de la casa.
french.DesktopIcon=Créer un raccourci sur le bureau
french.Autostart=Lancer Hearth à l'ouverture de session
french.TrustCert=Faire confiance au certificat de l'éditeur Hearth (facultatif ; aide Windows à reconnaître les mises à jour)
french.AdditionalOptions=Options supplémentaires :
french.OpenNow=Ouvrir Hearth maintenant
french.IconComment=Partagez des dossiers et discutez avec les autres PC de la maison.
german.DesktopIcon=Eine Desktopverknüpfung erstellen
german.Autostart=Hearth bei der Anmeldung starten
german.TrustCert=Dem Herausgeberzertifikat von Hearth vertrauen (optional; hilft Windows, Updates zu erkennen)
german.AdditionalOptions=Zusätzliche Optionen:
german.OpenNow=Hearth jetzt öffnen
german.IconComment=Ordner freigeben und mit den anderen PCs im Haus chatten.
brazilianportuguese.DesktopIcon=Criar um atalho na área de trabalho
brazilianportuguese.Autostart=Iniciar o Hearth quando eu entrar
brazilianportuguese.TrustCert=Confiar no certificado do publicador do Hearth (opcional; ajuda o Windows a reconhecer atualizações)
brazilianportuguese.AdditionalOptions=Opções adicionais:
brazilianportuguese.OpenNow=Abrir o Hearth agora
brazilianportuguese.IconComment=Compartilhe pastas e converse com os outros PCs da casa.
italian.DesktopIcon=Crea un collegamento sul desktop
italian.Autostart=Avvia Hearth all'accesso
italian.TrustCert=Considera attendibile il certificato dell'editore di Hearth (facoltativo; aiuta Windows a riconoscere gli aggiornamenti)
italian.AdditionalOptions=Opzioni aggiuntive:
italian.OpenNow=Apri Hearth adesso
italian.IconComment=Condividi cartelle e chatta con gli altri PC di casa.
russian.DesktopIcon=Создать ярлык на рабочем столе
russian.Autostart=Запускать Hearth при входе
russian.TrustCert=Доверять сертификату издателя Hearth (необязательно; помогает Windows узнавать обновления)
russian.AdditionalOptions=Дополнительные параметры:
russian.OpenNow=Открыть Hearth сейчас
russian.IconComment=Общие папки и чат с другими компьютерами в доме.
polish.DesktopIcon=Utwórz skrót na pulpicie
polish.Autostart=Uruchamiaj Hearth przy logowaniu
polish.TrustCert=Zaufaj certyfikatowi wydawcy Hearth (opcjonalnie; pomaga systemowi Windows rozpoznawać aktualizacje)
polish.AdditionalOptions=Opcje dodatkowe:
polish.OpenNow=Otwórz Hearth teraz
polish.IconComment=Udostępniaj foldery i rozmawiaj z innymi komputerami w domu.
japanese.DesktopIcon=デスクトップにショートカットを作成する
japanese.Autostart=サインイン時に Hearth を起動する
japanese.TrustCert=Hearth の発行元証明書を信頼する（任意。Windows が更新を認識しやすくなります）
japanese.AdditionalOptions=追加のオプション:
japanese.OpenNow=今すぐ Hearth を開く
japanese.IconComment=家のほかの PC とフォルダーを共有し、チャットします。
korean.DesktopIcon=바탕 화면에 바로 가기 만들기
korean.Autostart=로그인할 때 Hearth 시작
korean.TrustCert=Hearth 게시자 인증서 신뢰(선택 사항. Windows가 업데이트를 알아보는 데 도움이 됩니다)
korean.AdditionalOptions=추가 옵션:
korean.OpenNow=지금 Hearth 열기
korean.IconComment=집안의 다른 PC와 폴더를 공유하고 대화합니다.
chinesesimplified.DesktopIcon=创建桌面快捷方式
chinesesimplified.Autostart=登录时启动 Hearth
chinesesimplified.TrustCert=信任 Hearth 发布者证书（可选；帮助 Windows 识别更新）
chinesesimplified.AdditionalOptions=其他选项:
chinesesimplified.OpenNow=立即打开 Hearth
chinesesimplified.IconComment=与家里的其他电脑共享文件夹并聊天。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalOptions}"; Flags: checkedonce
Name: "autostart"; Description: "{cm:Autostart}"; GroupDescription: "{cm:AdditionalOptions}"; Flags: checkedonce
Name: "trustcert"; Description: "{cm:TrustCert}"; GroupDescription: "{cm:AdditionalOptions}"; Flags: unchecked

[Files]
Source: "..\dist\Hearth\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Hearth.cer"; DestDir: "{app}"; Flags: ignoreversion
Source: "trust-hearth-cert.cmd"; DestDir: "{app}"; Flags: ignoreversion
; Embedded for PrepareToInstall. Capture runs before taskkill.
; Silent relaunch is the [Run] entry below, not this script.
Source: "relaunch.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Comment: "{cm:IconComment}"; Check: NotInCi
Name: "{autodesktop}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Comment: "{cm:IconComment}"; Tasks: desktopicon; Check: NotInCi
; Autostart via the Startup folder, not a Run key (Run-key persistence is a Defender heuristic).
Name: "{userstartup}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"; Comment: "Hearth"; Tasks: autostart; Check: UserStartupIcon
Name: "{commonstartup}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"; Comment: "Hearth"; Tasks: autostart; Check: MachineStartupIcon

[Registry]
; Remove the v0.3.2 Run-key autostart if an older install left one behind.
; CI installs must not delete the interactive user's Run values.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hearth"; Flags: deletevalue; Check: NotInCi
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hearth"; Flags: deletevalue; Check: MachineStartupIcon

[Run]
; Silent relaunch is HiddenStart in [Code], not a [Run] entry. Inno opens a
; visible Setup window for every postinstall run entry, including nowait,
; and that window gets this install killed before Setup can exit.
; Interactive installs keep the post-install checkbox and the captured args.
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetRelaunchParams}"; Description: "{cm:OpenNow}"; Flags: nowait postinstall; Check: ShouldRelaunchInteractive

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  UserUninstallAttempted: Boolean;
  UserUninstallSaved: Boolean;
  ForeignUninstallSaved: Boolean;

function ShouldRelaunch: Boolean;
var
  I: Integer;
begin
  { CI silent smoke passes /SKIPRELAUNCH so Setup does not start Hearth
    against the machine's default profile. In-app updates omit the flag. }
  Result := True;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/SKIPRELAUNCH') = 0 then
    begin
      Result := False;
      Exit;
    end;
end;

function InCi: Boolean;
begin
  Result := CompareText(GetEnv('HEARTH_CI'), '1') = 0;
end;

function NotInCi: Boolean;
begin
  Result := not InCi;
end;

function UserStartupIcon: Boolean;
begin
  Result := (not InCi) and (not IsAdminInstallMode);
end;

function MachineStartupIcon: Boolean;
begin
  Result := (not InCi) and IsAdminInstallMode;
end;

function ShouldRelaunchInteractive: Boolean;
begin
  Result := ShouldRelaunch and not WizardSilent and not InCi;
end;

function CaptureFileExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\relaunch-cmdline.txt'));
end;

function PlainRelaunchAllowed: Boolean;
begin
  { CI must not start the real profile. HEARTH_HOME is an isolated one. }
  Result := (not InCi) or (GetEnv('HEARTH_HOME') <> '');
end;

function ShouldRelaunchSilent: Boolean;
begin
  Result := ShouldRelaunch and (WizardSilent or InCi) and (CaptureFileExists or PlainRelaunchAllowed);
end;

function CapturedRelaunchLine: String;
var
  Raw: AnsiString;
  CmdPath: String;
begin
  Result := '';
  CmdPath := ExpandConstant('{app}\relaunch-cmdline.txt');
  if not FileExists(CmdPath) then
    Exit;
  if LoadStringFromFile(CmdPath, Raw) then
    Result := Trim(String(Raw));
end;

function DropFlag(const Line, Flag: String): String;
var
  S, Token: String;
  I: Integer;
begin
  { Remove one CI-only flag. Quoted arguments stay intact. }
  S := Trim(Line);
  Result := '';
  I := 1;
  while I <= Length(S) do
  begin
    while (I <= Length(S)) and (S[I] = ' ') do
      I := I + 1;
    if I > Length(S) then
      Break;
    Token := '';
    if S[I] = '"' then
    begin
      repeat
        Token := Token + S[I];
        if (Length(Token) > 1) and (S[I] = '"') then
        begin
          I := I + 1;
          Break;
        end;
        I := I + 1;
      until I > Length(S);
      if Result <> '' then
        Result := Result + ' ';
      Result := Result + Token;
      Continue;
    end;
    while (I <= Length(S)) and (S[I] <> ' ') do
    begin
      Token := Token + S[I];
      I := I + 1;
    end;
    if CompareText(Token, Flag) = 0 then
      Continue;
    if Result <> '' then
      Result := Result + ' ';
    Result := Result + Token;
  end;
end;

function GetRelaunchParams(Value: String): String;
var
  Extra: String;
begin
  Extra := CapturedRelaunchLine;
  Result := '--updated {#MyAppVersion}';
  if Extra <> '' then
    Result := Result + ' ' + Extra;
  { Only CI adds --no-desktop and --no-browser. A silent update on a real
    PC must come back with the tray. A previous capture that hid the UI
    is stripped. A visible window during CI aborts the install on this PC. }
  if InCi then
  begin
    if Pos('--background', Result) = 0 then
      Result := Result + ' --background';
    if Pos('--no-browser', Result) = 0 then
      Result := Result + ' --no-browser';
    if Pos('--no-desktop', Result) = 0 then
      Result := Result + ' --no-desktop';
  end
  else
  begin
    Result := DropFlag(Result, '--no-desktop');
    Result := DropFlag(Result, '--no-browser');
  end;
  { WMI does not inherit this process environment. An isolated profile
    and port have to be on the command line or the app would open the
    real one. }
  if (GetEnv('HEARTH_HOME') <> '') and (Pos('--home', Result) = 0) then
    Result := Result + ' --home "' + GetEnv('HEARTH_HOME') + '"';
  if (GetEnv('HEARTH_PORT') <> '') and (Pos('--port', Result) = 0) then
    Result := Result + ' --port ' + GetEnv('HEARTH_PORT');
end;

procedure HiddenStart(const FileName, Params: String);
var
  Locator, Service, Startup, ProcClass, InParams, OutParams: Variant;
  Command: String;
  Ret: Integer;
begin
  { Return as soon as the process exists. Do not wait and do not stop it. }
  try
    if Params <> '' then
      Command := '"' + FileName + '" ' + Params
    else
      Command := '"' + FileName + '"';
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\cimv2');
    Startup := Service.Get('Win32_ProcessStartup').SpawnInstance_();
    Startup.ShowWindow := SW_HIDE;
    ProcClass := Service.Get('Win32_Process');
    InParams := ProcClass.Methods_.Item('Create').InParameters.SpawnInstance_();
    InParams.CommandLine := Command;
    InParams.ProcessStartupInformation := Startup;
    OutParams := ProcClass.ExecMethod_('Create', InParams);
    Ret := Integer(OutParams.ReturnValue);
    if Ret <> 0 then
      Log('HiddenStart create failed ' + IntToStr(Ret))
    else
      Log('HiddenStart pid ' + IntToStr(Integer(OutParams.ProcessId)));
  except
    Log('HiddenStart exception');
  end;
end;

function HiddenExec(const FileName, Params: String; const TimeoutMs: Integer): Boolean;
var
  Locator, Service, Startup, ProcClass, InParams, OutParams, Found: Variant;
  Command, Query: String;
  Pid, I, Limit, Ret: Integer;
begin
  { Do not wait inside Exec. A waiting call opens a visible Setup window
    and this install is killed before it can finish. }
  Result := False;
  try
    if Params <> '' then
      Command := '"' + FileName + '" ' + Params
    else
      Command := '"' + FileName + '"';
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\cimv2');
    Startup := Service.Get('Win32_ProcessStartup').SpawnInstance_();
    { ShowWindow hides the child. Leave startup flags unset: the process
      provider rejects the no-window creation flag and then runs nothing. }
    Startup.ShowWindow := SW_HIDE;
    ProcClass := Service.Get('Win32_Process');
    InParams := ProcClass.Methods_.Item('Create').InParameters.SpawnInstance_();
    InParams.CommandLine := Command;
    InParams.ProcessStartupInformation := Startup;
    OutParams := ProcClass.ExecMethod_('Create', InParams);
    Ret := Integer(OutParams.ReturnValue);
    if Ret <> 0 then
    begin
      Log('HiddenExec create failed ' + IntToStr(Ret));
      Exit;
    end;
    Pid := Integer(OutParams.ProcessId);
    Limit := TimeoutMs div 200;
    if Limit < 1 then
      Limit := 1;
    for I := 1 to Limit do
    begin
      Query := 'SELECT ProcessId FROM Win32_Process WHERE ProcessId=' + IntToStr(Pid);
      Found := Service.ExecQuery(Query);
      if Integer(Found.Count) = 0 then
      begin
        Result := True;
        Exit;
      end;
      Sleep(200);
    end;
    Log('HiddenExec timed out');
    try
      Found := Service.Get('Win32_Process.Handle="' + IntToStr(Pid) + '"');
      Found.Terminate(1);
    except
      Log('HiddenExec could not stop a timed-out process');
    end;
  except
    Log('HiddenExec exception');
    Result := False;
  end;
end;

procedure RunRelaunchScript(Mode, Updated: String);
var
  Params: String;
  Ok: Boolean;
begin
  { Capture running command lines before taskkill. Setup does not start the app from here. }
  try
    ExtractTemporaryFile('relaunch.ps1');
    Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\relaunch.ps1') + '" -Mode ' + Mode + ' -AppExe "' + ExpandConstant('{app}\{#MyAppExeName}') + '" -ArgsFile "' + ExpandConstant('{app}\relaunch-args.json') + '"';
    if Updated <> '' then
      Params := Params + ' -Updated "' + Updated + '"';
    Ok := HiddenExec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, 60000);
    if not Ok then
      Log('Capture did not finish; Setup will continue');
  except
    Log('Capture raised; Setup will continue');
  end;
end;

procedure Netsh(Params: String);
begin
  try
    if not HiddenExec(ExpandConstant('{sys}\netsh.exe'), Params, 30000) then
      Log('netsh did not finish');
  except
    Log('netsh failed');
  end;
end;

function UninstallSubkey: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E4C1F2A-9B3D-4A71-8E6C-2F5D9A1B0C44}_is1';
end;

function UninstallLocationIsThisApp: Boolean;
var
  Location: String;
begin
  Result := False;
  if not RegQueryStringValue(HKCU, UninstallSubkey, 'InstallLocation', Location) then
    Exit;
  Result := CompareText(AddBackslash(Location), AddBackslash(ExpandConstant('{app}'))) = 0;
end;

procedure SaveUserUninstallKey;
var
  KeyPath, OutFile: String;
begin
  if not InCi then
    Exit;
  UserUninstallAttempted := True;
  UserUninstallSaved := False;
  KeyPath := 'HKCU\' + UninstallSubkey;
  OutFile := ExpandConstant('{tmp}\hearth-user-uninstall.reg');
  if HiddenExec(ExpandConstant('{sys}\reg.exe'), 'export "' + KeyPath + '" "' + OutFile + '" /y', 15000) then
  begin
    if FileExists(OutFile) then
      UserUninstallSaved := True;
  end;
end;

procedure RestoreUserUninstallKey;
var
  KeyPath, OutFile: String;
begin
  if not InCi then
    Exit;
  if not UserUninstallAttempted then
    Exit;
  KeyPath := 'HKCU\' + UninstallSubkey;
  HiddenExec(ExpandConstant('{sys}\reg.exe'), 'delete "' + KeyPath + '" /f', 15000);
  if not UserUninstallSaved then
    Exit;
  OutFile := ExpandConstant('{tmp}\hearth-user-uninstall.reg');
  if FileExists(OutFile) then
    HiddenExec(ExpandConstant('{sys}\reg.exe'), 'import "' + OutFile + '"', 15000);
end;

procedure SaveForeignUninstallKey;
var
  KeyPath, OutFile: String;
begin
  if not RegKeyExists(HKCU, UninstallSubkey) then
    Exit;
  if UninstallLocationIsThisApp then
    Exit;
  KeyPath := 'HKCU\' + UninstallSubkey;
  OutFile := ExpandConstant('{tmp}\hearth-foreign-uninstall.reg');
  if HiddenExec(ExpandConstant('{sys}\reg.exe'), 'export "' + KeyPath + '" "' + OutFile + '" /y', 15000) then
  begin
    if FileExists(OutFile) then
      ForeignUninstallSaved := True;
  end;
end;

procedure RestoreForeignUninstallKey;
var
  KeyPath, OutFile: String;
begin
  if not ForeignUninstallSaved then
    Exit;
  OutFile := ExpandConstant('{tmp}\hearth-foreign-uninstall.reg');
  if not FileExists(OutFile) then
    Exit;
  KeyPath := 'HKCU\' + UninstallSubkey;
  HiddenExec(ExpandConstant('{sys}\reg.exe'), 'delete "' + KeyPath + '" /f', 15000);
  HiddenExec(ExpandConstant('{sys}\reg.exe'), 'import "' + OutFile + '"', 15000);
end;

procedure CloseLeftoverHearth;
var
  Locator, Service, Processes, Process: Variant;
  AppPrefix, ExePath: String;
  I, Pid, Count: Integer;
begin
  AppPrefix := Lowercase(AddBackslash(ExpandConstant('{app}')));
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\cimv2');
    Processes := Service.ExecQuery('SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name=''Hearth.exe''');
    Count := Integer(Processes.Count);
    for I := 0 to Count - 1 do
    begin
      Process := Processes.ItemIndex(I);
      ExePath := '';
      try
        ExePath := Lowercase(String(Process.ExecutablePath));
      except
        ExePath := '';
      end;
      if (ExePath <> '') and (Pos(AppPrefix, ExePath) = 1) then
      begin
        Pid := Integer(Process.ProcessId);
        if not HiddenExec(ExpandConstant('{sys}\taskkill.exe'), '/PID ' + IntToStr(Pid) + ' /T /F', 15000) then
          Log('taskkill did not finish for pid ' + IntToStr(Pid));
      end;
    end;
  except
    Log('Scoped taskkill failed; Setup will continue');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  try
    SaveUserUninstallKey;
  except
    Log('Could not save the existing uninstall key; Setup will continue');
  end;
  { Snapshot --home and --port BEFORE the scoped taskkill, so an older
    Hearth that does not persist args still relaunches the same profile. }
  try
    RunRelaunchScript('Capture', '');
  except
    Log('Capture failed; Setup will continue and relaunch hidden');
  end;
  { Wait briefly for a graceful shutdown, then stop only a Hearth.exe
    whose path is under this install. Never match on the image name. }
  try
    Sleep(1500);
    CloseLeftoverHearth;
    Sleep(500);
  except
    Log('Scoped taskkill failed; Setup will continue');
  end;
end;

procedure RemoveStaleMachineCopy();
var
  OldDir: String;
begin
  { An older all-users install lives under Program Files and has its own
    Start menu and Startup shortcuts. Never run its uninstaller: that
    asks for elevation. Delete the shared shortcuts. Leave the folder. }
  DeleteFile(ExpandConstant('{commonprograms}\Hearth.lnk'));
  DeleteFile(ExpandConstant('{commonstartup}\Hearth.lnk'));
  DeleteFile(ExpandConstant('{commondesktop}\Hearth.lnk'));
  DeleteFile(ExpandConstant('{commonprograms}\Hearth\Hearth.lnk'));
  OldDir := ExpandConstant('{autopf}\Hearth');
  if CompareText(OldDir, ExpandConstant('{app}')) = 0 then
    Exit;
  if DirExists(OldDir) then
    Log('Stale Program Files copy was left in place (not writable, no elevation)');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RemoveStaleMachineCopy();
    if IsAdminInstallMode then
    begin
      Netsh('advfirewall firewall delete rule name="Hearth UI and file sharing"');
      Netsh('advfirewall firewall delete rule name="Hearth discovery"');
      Netsh('advfirewall firewall add rule name="Hearth UI and file sharing" dir=in action=allow protocol=TCP localport=7474 profile=private');
      Netsh('advfirewall firewall add rule name="Hearth discovery" dir=in action=allow protocol=UDP localport=45454 profile=private');
    end;
    if WizardIsTaskSelected('trustcert') then
    begin
      try
        if IsAdminInstallMode then
          HiddenExec(ExpandConstant('{sys}\certutil.exe'), '-addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', 30000)
        else
          HiddenExec(ExpandConstant('{sys}\certutil.exe'), '-user -addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', 30000);
      except
        Log('certutil failed; Setup will continue');
      end;
    end;
    if ShouldRelaunchSilent then
    begin
      try
        HiddenStart(ExpandConstant('{app}\{#MyAppExeName}'), GetRelaunchParams(''));
      except
        Log('Relaunch failed; Setup will continue');
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    SaveForeignUninstallKey;
    if IsAdminInstallMode then
    begin
      Netsh('advfirewall firewall delete rule name="Hearth UI and file sharing"');
      Netsh('advfirewall firewall delete rule name="Hearth discovery"');
    end;
  end;
  if CurUninstallStep = usPostUninstall then
    RestoreForeignUninstallKey;
end;

procedure DeinitializeSetup();
begin
  RestoreUserUninstallKey;
end;

procedure DeinitializeUninstall();
begin
  RestoreForeignUninstallKey;
end;
