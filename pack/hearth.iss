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
; Silent updates relaunch hidden. A capture file keeps --home and --port.
; With no capture file, a normal install still starts Hearth hidden.
; CI does that only when HEARTH_HOME is set, so the real profile stays untouched.
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

function GetRelaunchParams(Value: String): String;
var
  Extra: String;
begin
  Extra := CapturedRelaunchLine;
  Result := '--updated {#MyAppVersion}';
  if Extra <> '' then
    Result := Result + ' ' + Extra
  else if WizardSilent or InCi then
    Result := Result + ' --background --no-browser --no-desktop';
  if InCi then
  begin
    if Pos('--no-desktop', Result) = 0 then
      Result := Result + ' --no-desktop';
    if Pos('--no-browser', Result) = 0 then
      Result := Result + ' --no-browser';
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

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  { Snapshot --home and --port BEFORE taskkill, so an older Hearth that
    does not persist args still relaunches the same profile. }
  try
    RunRelaunchScript('Capture', '');
  except
    Log('Capture failed; Setup will continue and relaunch hidden');
  end;
  { The running app exits itself and drops its per-profile instance lock
    before Setup starts. Wait briefly for a graceful shutdown, then
    force-kill leftover Hearth.exe so a stale process cannot block the
    relaunch and no close-app dialog appears. }
  try
    Sleep(1500);
    if not HiddenExec(ExpandConstant('{sys}\taskkill.exe'), '/IM Hearth.exe /F /T', 30000) then
      Log('taskkill did not finish; Setup will continue');
    Sleep(500);
  except
    Log('taskkill failed; Setup will continue');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
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
      try
        if IsAdminInstallMode then
          HiddenExec(ExpandConstant('{sys}\certutil.exe'), '-addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', 30000)
        else
          HiddenExec(ExpandConstant('{sys}\certutil.exe'), '-user -addstore TrustedPublisher "' + ExpandConstant('{app}\Hearth.cer') + '"', 30000);
      except
        Log('certutil failed; Setup will continue');
      end;
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
