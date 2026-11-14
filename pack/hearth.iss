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
Filename: "{app}\{#MyAppExeName}"; Parameters: "--updated {#MyAppVersion}"; Description: "Open Hearth now"; Flags: nowait postinstall; Check: ShouldRelaunch

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
