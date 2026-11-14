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
; close leftovers without asking, and the app also exits itself first.
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

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"; Flags: checkedonce
Name: "autostart"; Description: "Start Hearth when I sign in"; GroupDescription: "Additional options:"; Flags: checkedonce
Name: "trustcert"; Description: "Trust the Hearth publisher certificate (optional; helps Windows recognize updates)"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
Source: "..\dist\Hearth\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Hearth.cer"; DestDir: "{app}"; Flags: ignoreversion
Source: "trust-hearth-cert.cmd"; DestDir: "{app}"; Flags: ignoreversion
; Embedded for PrepareToInstall. Capture runs before taskkill.
; Silent relaunch is the [Run] entry below, not this script.
Source: "relaunch.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Comment: "Share folders and chat with the other PCs in the house"
Name: "{autodesktop}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Comment: "Share folders and chat with the other PCs in the house"; Tasks: desktopicon
; Autostart via the Startup folder, not a Run key (Run-key persistence is a Defender heuristic).
Name: "{userstartup}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"; Comment: "Hearth"; Tasks: autostart; Check: not IsAdminInstallMode
Name: "{commonstartup}\Hearth"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"; Comment: "Hearth"; Tasks: autostart; Check: IsAdminInstallMode

[Registry]
; Remove the v0.3.2 Run-key autostart if an older install left one behind.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hearth"; Flags: deletevalue
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Hearth"; Flags: deletevalue; Check: IsAdminInstallMode

[Run]
; Silent updates relaunch hidden with the captured command line.
; No capture file means do not start a default-profile instance.
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetRelaunchParams}"; Flags: nowait postinstall runhidden; Check: ShouldRelaunchSilent
; Interactive installs keep the post-install checkbox and the same captured args.
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetRelaunchParams}"; Description: "Open Hearth now"; Flags: nowait postinstall; Check: ShouldRelaunchInteractive

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
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

function ShouldRelaunchInteractive: Boolean;
begin
  Result := ShouldRelaunch and not WizardSilent and not InCi;
end;

function ShouldRelaunchSilent: Boolean;
begin
  { A missing capture file must not start a default-profile instance. }
  Result := ShouldRelaunch and (WizardSilent or InCi) and FileExists(ExpandConstant('{app}\relaunch-cmdline.txt'));
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

function GetRelaunchParams(Value: String): String;
var
  Extra: String;
begin
  Extra := CapturedRelaunchLine;
  Result := '--updated {#MyAppVersion}';
  if Extra <> '' then
    Result := Result + ' ' + Extra;
  if InCi then
  begin
    if Pos('--no-desktop', Result) = 0 then
      Result := Result + ' --no-desktop';
    if Pos('--no-browser', Result) = 0 then
      Result := Result + ' --no-browser';
  end;
end;

procedure RunRelaunchScript(Mode, Updated: String);
var
  ResultCode: Integer;
  Params: String;
begin
  { Capture running command lines before taskkill. Setup does not start the app from here. }
  ExtractTemporaryFile('relaunch.ps1');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\relaunch.ps1') + '" -Mode ' + Mode + ' -AppExe "' + ExpandConstant('{app}\{#MyAppExeName}') + '" -ArgsFile "' + ExpandConstant('{app}\relaunch-args.json') + '"';
  if Updated <> '' then
    Params := Params + ' -Updated "' + Updated + '"';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure Netsh(Params: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\netsh.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  { Snapshot --home/--port from the running exe (or the file the app
    already wrote) BEFORE taskkill, so an older Hearth that does not
    persist args still relaunches the same profile with no data loss. }
  RunRelaunchScript('Capture', '');
  { The running app exits itself and drops its per-profile instance lock
    before Setup starts. Wait briefly for a graceful shutdown, then
    force-kill leftover Hearth.exe so a stale process cannot block the
    relaunch and no close-app dialog appears. }
  Sleep(1500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM Hearth.exe /F /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if IsAdminInstallMode then
    begin
      Netsh('advfirewall firewall delete rule name="Hearth UI and file sharing"');
      Netsh('advfirewall firewall delete rule name="Hearth discovery"');
      Netsh('advfirewall firewall add rule name="Hearth UI and file sharing" dir=in action=allow protocol=TCP localport=7474 profile=private');
      Netsh('advfirewall firewall add rule name="Hearth discovery" dir=in action=allow protocol=UDP localport=45454 profile=private');
    end;
    if WizardIsTaskSelected('trustcert') then
    begin
      if IsAdminInstallMode then
        Exec(ExpandConstant('{sys}\certutil.exe'), ' -addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
      else
        Exec(ExpandConstant('{sys}\certutil.exe'), ' -user -addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
    { The [Run] entry relaunches Hearth. Do not start it from here. }
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and IsAdminInstallMode then
  begin
    Netsh('advfirewall firewall delete rule name="Hearth UI and file sharing"');
    Netsh('advfirewall firewall delete rule name="Hearth discovery"');
  end;
end;
